# Production through a Cloudflare Tunnel (home server, beside the development copy)

This runbook puts the two production sites on the internet through a Cloudflare Tunnel:

- applicants: `https://apply.evsuocc-recruitment.online`;
- staff: `https://staff.evsuocc-recruitment.online`;
- the bare domain and `www` redirect to the applicant site.

The home server keeps its development copy (`/srv/evsu-recruitment`, port 8000, home network only)
unchanged. Production is a separate installation with its own account, folders, database,
settings, and services.

It replaces the certificate, port-forwarding, and firewall parts of [DEPLOY.md](DEPLOY.md). Every
other rule there still applies. Until the Data Privacy Officer approves real data, use **made-up
accounts and applicants only**.

## Layout

```
Visitor --HTTPS--> Cloudflare (certificates; apply. / staff.)
                      |  tunnel: the server connects out; no incoming ports
                      v
             cloudflared (this server)
                      v
             Nginx on 127.0.0.1:8080  (picks the site by host name; real visitor address)
              |-> Gunicorn evsu-prod@public  (unix:/run/evsu-prod-public/gunicorn.sock)
              '-> Gunicorn evsu-prod@staff   (unix:/run/evsu-prod-staff/gunicorn.sock)
                      v
             MySQL database evsu_recruitment (production)
```

| | Development copy | Production |
| --- | --- | --- |
| Folder | `/srv/evsu-recruitment` | `/srv/evsu-prod` |
| Account | `evsu` | `evsuprod` |
| Database | `evsu_recruitment_dev` | `evsu_recruitment` |
| Per-site settings | (none) | `/etc/evsu-prod/public.env`, `staff.env` |
| Web | `evsu-dev` (runserver, port 8000, home network) | `evsu-prod@public`, `evsu-prod@staff` (Gunicorn) |
| AI worker | `evsu-ai-worker` | `evsu-prod-ai-worker` |
| Timers | `evsu-notifications.timer` | `evsu-prod-notifications.timer`, `evsu-prod-backup.timer` |
| Backups | (none) | `/srv/evsu-prod-backups` |

Files used from this repository: `deploy/tunnel/` (Nginx, systemd units, per-site settings) and
`.env.production.example`.

**Who does what.** The person running the setup can do most steps. Three steps are only for the
project owner, because they involve secrets:

- typing the MySQL root password (step 3);
- entering the SMTP app password (step 4);
- running the Cloudflare tunnel token (step 8).

Helper scripts in `deploy/tunnel/scripts/` do those steps without showing any secret. Install each
one as root (`sudo install -o root -g root -m 700 <script> /usr/local/sbin/`):

| Script | Step | What it does |
| --- | --- | --- |
| `evsu-prod-create-db` | 3 | Generates the two account passwords into `/etc/evsu-prod/db.secrets` (root only) and creates the database; only the MySQL root password is typed |
| `make_prod_env.py` | 4 | Run as root: writes `/srv/evsu-prod/.env` with a new secret key, the database password, the production paths and addresses, and the sender settings from the development copy (email password left empty) |
| `evsu-prod-set-smtp` | 4 | Asks for the email app password without showing it, restarts the sites, turns on the email retry timer, and sends a test email |
| `evsu-prod-create-admin` | 5 | Creates the first administrator and gives it the System Administrator role |

**The production folder is private.** `/srv/evsu-prod` belongs to `evsuprod` (mode 750), so an
administrator account cannot `cd` into it or expand `*` inside it. Run commands as
`sudo -u evsuprod bash -c 'cd /srv/evsu-prod && ...'`, and copy files out with
`sudo bash -c 'cp /srv/evsu-prod/... ...'`. The commands below are written that way where it
matters.

## 1. Packages

```bash
sudo apt update
sudo apt install nginx clamav-daemon
sudo systemctl enable --now clamav-daemon     # first start downloads signatures (a few minutes)
clamdscan --version

# cloudflared from Cloudflare's package repository
sudo mkdir -p --mode=0755 /usr/share/keyrings
curl -fsSL https://pkg.cloudflare.com/cloudflare-main.gpg | sudo tee /usr/share/keyrings/cloudflare-main.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/cloudflare-main.gpg] https://pkg.cloudflare.com/cloudflared any main" \
    | sudo tee /etc/apt/sources.list.d/cloudflared.list
sudo apt update && sudo apt install cloudflared
cloudflared --version
```

Nginx starts with a default site on port 80. Remove it (step 7); port 80 is closed in step 9 anyway.

ClamAV uses about 1 GB of memory once its signatures are loaded.

## 2. Account, folders, and code

```bash
sudo adduser --system --group --home /srv/evsu-prod evsuprod
sudo usermod -aG evsuprod www-data            # Nginx reads static and protected files

# GitHub read-only deploy key: a copy that only evsuprod can read
sudo mkdir -p /etc/evsu-prod
sudo install -o evsuprod -g evsuprod -m 600 /etc/evsu-recruitment/github_deploy_key /etc/evsu-prod/
sudo install -o evsuprod -g evsuprod -m 644 /etc/evsu-recruitment/github_known_hosts /etc/evsu-prod/
printf '#!/bin/sh\nexec ssh -i /etc/evsu-prod/github_deploy_key -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=/etc/evsu-prod/github_known_hosts "$@"\n' \
    | sudo tee /etc/evsu-prod/git-ssh.sh >/dev/null
sudo chown root:evsuprod /etc/evsu-prod/git-ssh.sh && sudo chmod 750 /etc/evsu-prod/git-ssh.sh
sudo chown root:evsuprod /etc/evsu-prod && sudo chmod 750 /etc/evsu-prod

sudo -u evsuprod env GIT_SSH=/etc/evsu-prod/git-ssh.sh \
    git clone git@github.com:Mike-meowmeow/app.git /srv/evsu-prod
sudo -u evsuprod git -C /srv/evsu-prod config core.sshCommand /etc/evsu-prod/git-ssh.sh

sudo -u evsuprod mkdir -p /srv/evsu-prod/private/{documents,exports,reports,templates}
sudo chmod -R 750 /srv/evsu-prod/private
sudo mkdir -p /srv/evsu-prod-backups /var/log/evsu-prod
sudo chown evsuprod:evsuprod /srv/evsu-prod-backups /var/log/evsu-prod
sudo chmod 750 /srv/evsu-prod-backups
```

**AI models.** Both installations read the same offline models in `/srv/evsu-models`. Give them a
shared group, so that neither account can read the other's files:

```bash
sudo groupadd --system evsu-models
sudo usermod -aG evsu-models evsu
sudo usermod -aG evsu-models evsuprod
sudo chgrp -R evsu-models /srv/evsu-models
sudo chmod -R g+rX /srv/evsu-models
sudo systemctl restart evsu-ai-worker         # picks up the new group
```

## 3. Database (the project owner types the MySQL root password)

```bash
sudo mysql -u root -p
```

```sql
CREATE DATABASE evsu_recruitment CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'evsu_app'@'localhost' IDENTIFIED BY '<app password>';
GRANT SELECT, INSERT, UPDATE, DELETE, LOCK TABLES, SHOW VIEW, TRIGGER
    ON evsu_recruitment.* TO 'evsu_app'@'localhost';
CREATE USER 'evsu_migrate'@'localhost' IDENTIFIED BY '<migration password>';
GRANT ALL PRIVILEGES ON evsu_recruitment.* TO 'evsu_migrate'@'localhost';
GRANT ALL PRIVILEGES ON evsu_recruitment_restore.* TO 'evsu_app'@'localhost';
```

Generate both passwords with `python3 -c "import secrets; print(secrets.token_urlsafe(24))"`, and
keep them out of chat and notes. The development database and its account are not touched.

## 4. Python environment and settings

```bash
sudo systemctl enable --now clamav-daemon     # once freshclam has downloaded the signatures
sudo -u evsuprod bash -ec '
cd /srv/evsu-prod
python3.12 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements-linux.txt
.venv/bin/pip install torch==2.15.0 --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -r requirements-ai.txt
.venv/bin/python -m spacy download en_core_web_sm-3.8.0 --direct
.venv/bin/python -m pip check
'
```

The system packages (AI-SERVER.md 5.1) and the downloaded SBERT model (5.5) are already on this
server. Compare `pip freeze` with the development copy afterwards. On 26 November 2026 only two
unpinned indirect packages differed by a patch version (`regex`, `typer`).

Then the settings file. `scripts/make_prod_env.py` (run as root) writes it from
`.env.production.example`; afterwards the project owner runs `evsu-prod-set-smtp` for the email
password. To write it by hand instead, these are the values to set in `/srv/evsu-prod/.env`;
everything else stays as in the example:

```ini
DJANGO_SECRET_KEY=<new: python3 -c "import secrets; print(secrets.token_urlsafe(64))">
PUBLIC_SITE_URL=https://apply.evsuocc-recruitment.online
STAFF_SITE_URL=https://staff.evsuocc-recruitment.online
BEHIND_PROXY=True
TRUSTED_PROXIES=127.0.0.1,unix
DB_NAME=evsu_recruitment
DB_USER=evsu_app
DB_PASSWORD=<app password from step 3>
PRIVATE_STORAGE_ROOT=/srv/evsu-prod/private
STATIC_ROOT=/srv/evsu-prod/staticfiles
BACKUP_ROOT=/srv/evsu-prod-backups
LOG_DIR=/var/log/evsu-prod
EMAIL_HOST=smtp.gmail.com
EMAIL_HOST_USER=<sending Gmail address>
EMAIL_HOST_PASSWORD=<NEW Gmail app password: the project owner types it here>
DEFAULT_FROM_EMAIL=EVSU-Ormoc Recruitment <the sending address>
PRIVACY_NOTICE_VERSION=<notice version>
# Start with a short HSTS time while testing; raise to 31536000 once stable.
SECURE_HSTS_SECONDS=3600
# AI, as on the development copy, once step 4's AI packages are installed:
HF_HOME=/srv/evsu-models
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
AI_EXTRACTION_ENABLED=True
AI_COMPETENCY_MATCHING_ENABLED=True
ASSISTANT_ENABLED=True
```

`TRUSTED_PROXIES=127.0.0.1,unix` is required. Gunicorn on a Unix socket reports no client
address. Without `unix`, the audit trail would record no address and the per-address sign-in
limits would not work.

Per-site settings:

```bash
sudo cp /srv/evsu-prod/deploy/tunnel/public.env /etc/evsu-prod/public.env
sudo cp /srv/evsu-prod/deploy/tunnel/staff.env /etc/evsu-prod/staff.env
sudo chown root:evsuprod /etc/evsu-prod/public.env /etc/evsu-prod/staff.env
sudo chmod 640 /etc/evsu-prod/public.env /etc/evsu-prod/staff.env
```

## 5. Migrate, seed, static files, checks

As root, so the migration password is read from `/etc/evsu-prod/db.secrets` and never typed:

```bash
sudo bash -ec '
. /etc/evsu-prod/db.secrets
cd /srv/evsu-prod
sudo -u evsuprod env DB_USER=evsu_migrate DB_PASSWORD="$DB_MIGRATE_PASSWORD" \
    .venv/bin/python manage.py migrate --noinput
sudo -u evsuprod .venv/bin/python manage.py seed_reference_data
sudo -u evsuprod .venv/bin/python manage.py collectstatic --noinput
for site in public staff; do
  sudo -u evsuprod env SITE_PORTAL=$site $(grep -v "^#" /etc/evsu-prod/$site.env | xargs) \
      .venv/bin/python manage.py check --deploy
done
'
```

`check --deploy` must report no issues for either site. Then the project owner creates the
administrator and sets its password:

```bash
sudo /usr/local/sbin/evsu-prod-create-admin
```

Use made-up names and data for every other account until the Data Privacy Officer approves. Do
not register the official roster template with real staff names on this server.

## 6. Services and timers

```bash
sudo bash -c 'cp /srv/evsu-prod/deploy/tunnel/systemd/* /etc/systemd/system/'
sudo systemctl daemon-reload
sudo systemctl enable --now evsu-prod@public evsu-prod@staff
sudo systemctl enable --now evsu-prod-ai-worker evsu-prod-backup.timer
systemctl is-active evsu-prod@public evsu-prod@staff evsu-prod-ai-worker
```

Leave `evsu-prod-notifications.timer` off until the email password is set. `evsu-prod-set-smtp`
turns it on; otherwise retries are used up on emails that cannot be sent.

Two AI workers (development and production) each use up to about 600 MB while working. If memory
is tight, stop the development worker while it is not needed: `sudo systemctl stop evsu-ai-worker`.

## 7. Nginx

```bash
sudo rm -f /etc/nginx/sites-enabled/default
sudo cp /srv/evsu-prod/deploy/tunnel/nginx-tunnel-proxy.conf /etc/nginx/snippets/evsu-tunnel-proxy.conf
sudo cp /srv/evsu-prod/deploy/tunnel/nginx-tunnel.conf /etc/nginx/sites-available/evsu-tunnel
sudo ln -sf /etc/nginx/sites-available/evsu-tunnel /etc/nginx/sites-enabled/evsu-tunnel
# restart, not reload: a reload kept the default site's port 80 socket open
sudo nginx -t && sudo systemctl restart nginx
sudo ss -tlnp | grep nginx                      # must show 127.0.0.1:8080 only
curl -sI -H 'Host: apply.evsuocc-recruitment.online' http://127.0.0.1:8080/ | head -1
curl -sI -H 'Host: staff.evsuocc-recruitment.online' http://127.0.0.1:8080/ | head -1
```

Both requests should answer (200, or a redirect to a sign-in page). Nothing is on the internet yet.

## 8. The tunnel (the project owner, in the Cloudflare dashboard)

1. Cloudflare dashboard → **Zero Trust** → **Networks** → **Tunnels** → **Create a tunnel** →
   **Cloudflared**. Name it `evsu-server`.
2. On "Install and run a connector", choose **Debian**. The first box (installing cloudflared)
   was already done in step 1; running it again only says "already the newest version". Copy only the command
   `sudo cloudflared service install <token>`, then run it **yourself** on the server over SSH. The
   token is a secret: do not paste it into chat, notes, or the repository.
3. Back in the dashboard, add **Public hostnames** (newer dashboards: **Published application
   routes**), each with type **HTTP** and URL
   `127.0.0.1:8080`:
   - `apply.evsuocc-recruitment.online`
   - `staff.evsuocc-recruitment.online`
   - `evsuocc-recruitment.online` (redirected to `apply.` by Nginx)
4. **SSL/TLS** → **Edge Certificates**: turn on **Always Use HTTPS**, and set **Minimum TLS
   Version** to 1.2.
5. Leave caching at the default. Cloudflare does not cache the pages, which are HTML; static files
   may be cached.

Check on the server: `systemctl is-active cloudflared` shows `active`, and the dashboard shows the
tunnel as **Healthy**.

## 9. Firewall and housekeeping

No incoming web ports are needed. Close them and keep SSH:

```bash
sudo ufw delete allow 80/tcp
sudo ufw delete allow 443/tcp
sudo ufw status            # OpenSSH and 8000 (home network only) remain
```

Optionally limit SSH to the home network too: `sudo ufw allow from 192.168.1.0/24 to any port 22`,
then `sudo ufw delete allow OpenSSH`. Do this only from a computer on the home network, and keep a
keyboard and screen ready in case you lock yourself out.

At the very end of the setup:

- remove the temporary setup rule: `sudo rm /etc/sudoers.d/90-setup-temp`;
- confirm the Gmail app password and the SSH key passphrase were changed (both were shared in chat
  earlier).

## 10. Verify

Use a phone on **mobile data**, not the home Wi-Fi. Then run DEPLOY.md step 7 with
`apply.evsuocc-recruitment.online` and `staff.evsuocc-recruitment.online`. In particular:

1. **Applicant site:**
   - `http://apply...` becomes `https://`;
   - registration, email verification, sign-in, and upload work;
   - the EICAR test file is quarantined;
   - the staff paths answer 404.
2. **Staff site:** staff sign-in with two-factor works, and an applicant account is refused.
3. **Real visitor addresses (step 7.9):** **System Admin → Audit trail** shows the phone's public
   address for its sign-in. It must not show `127.0.0.1` or nothing.
4. **Sign-in limits:** five wrong passwords show "Too many failed sign-in attempts".
5. **Backups:** run one at once instead of waiting for the night:

   ```bash
   sudo -u evsuprod bash -c 'cd /srv/evsu-prod && .venv/bin/python manage.py backup_create \
       && .venv/bin/python manage.py backup_verify \
       && .venv/bin/python manage.py restore_check --requested-by <admin username>'
   ```

   It ends with "Restore check of ... passed". **Backups and restore checks** on the staff site
   shows both.
6. The development copy still works on `http://192.168.1.107:8000` from the home network, and not
   from mobile data.

## 11. Updating production

Try every update on the development copy first (RELEASE.md). Then:

```bash
cd /srv/evsu-prod
sudo -u evsuprod .venv/bin/python manage.py backup_create      # always back up first
sudo -u evsuprod git pull
sudo -u evsuprod .venv/bin/pip install -r requirements-linux.txt
sudo -u evsuprod env DB_USER=evsu_migrate DB_PASSWORD='<migration password>' \
    .venv/bin/python manage.py migrate
sudo -u evsuprod .venv/bin/python manage.py collectstatic --noinput
sudo systemctl reload evsu-prod@public evsu-prod@staff
sudo systemctl restart evsu-prod-ai-worker
```

## Taking it offline

- **Quickly:** `sudo systemctl stop cloudflared`. Both sites disappear from the internet at once,
  and nothing else changes.
- **Completely:**
  - delete the tunnel in the dashboard;
  - stop and disable the `evsu-prod*` units;
  - remove `/etc/nginx/sites-enabled/evsu-tunnel`.

  The development copy is unaffected.

## Privacy note

Cloudflare ends the HTTPS connection at its edge, so it can see the pages and files that pass
through the tunnel. Include this in the hosting note for the Data Privacy Officer before any real
applicant data is used.
