# Deploying to a Linux staging or production server

This runbook sets up the EVSU-Ormoc recruitment system on Ubuntu with Nginx, Gunicorn, and MySQL.
Staging and production follow the same steps with different host names and credentials. It assumes
a server administrator with `sudo`. Paths below use `/srv/evsu-recruitment`.

The steps describe the intended setup; they have not yet been run on a server. Record the versions
you actually install in the project's Development Logs.

**Home server behind a Cloudflare Tunnel** (no public IP, no certificates on the server, beside the
development copy): follow [TUNNEL.md](TUNNEL.md). It uses the files in `deploy/tunnel/` and replaces
the certificate, port-forwarding, and firewall parts of this guide.

**Client addresses:** Gunicorn on a Unix socket reports no client address, so `TRUSTED_PROXIES` must
include `unix` (as in `.env.production.example`). Otherwise the audit trail records no address and
the per-address sign-in limits do not apply.

## Layout: one internet server, two sites

| | Public applicant site | Staff site |
| --- | --- | --- |
| Example address | `https://apply.example.edu.ph` | `https://recruit-staff.example.edu.ph` |
| Reachable from | The internet | The internet (no address restriction) |
| Service | `evsu-recruitment@public` | `evsu-recruitment@staff` |
| Nginx ports | 80, 443 (by host name) | 80, 443 (by host name) |
| Accounts | Applicants only | Staff only |

Both sites use the same code, MySQL database, and private files, so there is nothing to synchronize.
They stay separate by host name: applicant accounts cannot sign in on the staff site, staff accounts
cannot sign in on the applicant site, and each site answers 404 for the other's pages.

The server can be a rented cloud server (used for testing and the defense) or a school server with a
public address. **You need before starting:** a server with a fixed public IP, at least 8 GB of RAM
(ClamAV, MySQL, both sites, and later the AI worker), a domain with two DNS `A` records (applicant
and staff host names) pointing to that IP, and an SMTP account. Use synthetic test data only on a
rented server until the school's Data Privacy Officer approves where real applicant data is stored.

## 1. Server packages

Use a supported Ubuntu LTS release and MySQL 8.4 LTS (Django 5.2 needs MySQL 8.0.11 or newer;
8.0 is end-of-life).

```bash
sudo apt update
sudo apt install python3.12 python3.12-venv python3.12-dev build-essential pkg-config \
    default-libmysqlclient-dev mysql-server mysql-client nginx git clamav-daemon
```

Load MySQL time zone tables once (the application converts times to Asia/Manila in SQL):

```bash
mysql_tzinfo_to_sql /usr/share/zoneinfo | sudo mysql mysql
```

## 2. Accounts, folders, and code

```bash
sudo adduser --system --group --home /srv/evsu-recruitment evsu
sudo usermod -aG evsu www-data                  # Nginx reads static and protected files
sudo -u evsu git clone https://github.com/Mike-meowmeow/app.git /srv/evsu-recruitment
sudo mkdir -p /srv/evsu-recruitment-backups /var/log/evsu-recruitment
sudo chown evsu:evsu /srv/evsu-recruitment-backups /var/log/evsu-recruitment
sudo chmod 750 /srv/evsu-recruitment-backups
```

Create the private storage folders and keep them out of every public location:

```bash
sudo -u evsu mkdir -p /srv/evsu-recruitment/private/{documents,exports,reports,templates}
sudo chmod -R 750 /srv/evsu-recruitment/private
```

## 3. Database

Create the database and two accounts: one for migrations, one for the running application.

```sql
CREATE DATABASE evsu_recruitment CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER 'evsu_app'@'localhost' IDENTIFIED BY '<app password>';
GRANT SELECT, INSERT, UPDATE, DELETE, LOCK TABLES, SHOW VIEW, TRIGGER
    ON evsu_recruitment.* TO 'evsu_app'@'localhost';
CREATE USER 'evsu_migrate'@'localhost' IDENTIFIED BY '<migration password>';
GRANT ALL PRIVILEGES ON evsu_recruitment.* TO 'evsu_migrate'@'localhost';
-- isolated database for restore checks (see RECOVERY.md)
GRANT ALL PRIVILEGES ON evsu_recruitment_restore.* TO 'evsu_app'@'localhost';
```

`LOCK TABLES`, `SHOW VIEW`, and `TRIGGER` let the application account run `mysqldump` for backups.
Run migrations with the migration account by overriding `DB_USER`/`DB_PASSWORD` for that command
only (step 5).

## 4. Python environment and settings

```bash
cd /srv/evsu-recruitment
sudo -u evsu python3.12 -m venv .venv
sudo -u evsu .venv/bin/pip install -r requirements-linux.txt
sudo -u evsu cp .env.production.example .env
sudo -u evsu nano .env           # site addresses, secret key, passwords, SMTP
sudo chmod 600 .env

sudo mkdir -p /etc/evsu-recruitment
sudo cp deploy/public.env.example /etc/evsu-recruitment/public.env
sudo cp deploy/staff.env.example /etc/evsu-recruitment/staff.env
sudo nano /etc/evsu-recruitment/public.env   # public host name
sudo nano /etc/evsu-recruitment/staff.env    # staff host name
```

A web process refuses to start when `DJANGO_DEBUG=False` and `SITE_PORTAL` is not `public` or
`staff`. The public site also refuses to start without `MALWARE_SCAN_COMMAND`, and refuses any upload
it could not scan. Start ClamAV and confirm it works: `sudo systemctl enable --now clamav-daemon`,
then `clamdscan --version`.

`DJANGO_DEBUG=False` turns on HTTPS redirects, secure cookies, and HSTS. `PRIVATE_FILE_DELIVERY=nginx`
makes downloads go through the protected Nginx location after Django's permission check.

## 5. Migrate, seed, static files, checks

```bash
cd /srv/evsu-recruitment
sudo -u evsu env DB_USER=evsu_migrate DB_PASSWORD='<migration password>' \
    .venv/bin/python manage.py migrate
sudo -u evsu .venv/bin/python manage.py seed_reference_data
sudo -u evsu .venv/bin/python manage.py collectstatic --noinput
for site in public staff; do
  sudo -u evsu env SITE_PORTAL=$site $(sudo grep -v '^#' /etc/evsu-recruitment/$site.env | xargs) \
      .venv/bin/python manage.py check --deploy
done
sudo -u evsu .venv/bin/python -m pip check
```

`check --deploy` must report no issues for either site. Then create the first administrator and register the
official roster template (it is not in the repository):

```bash
sudo -u evsu .venv/bin/python manage.py createsuperuser
sudo -u evsu .venv/bin/python manage.py assign_role <admin> "System Administrator"
sudo -u evsu .venv/bin/python manage.py register_roster_template \
    "/path/to/MATRIX_TEACHING APPLICANTS FOR PSB.xlsx" --approve-as <approver>
```

## 6. Gunicorn, Nginx, and scheduled jobs

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now evsu-recruitment@public evsu-recruitment@staff
sudo systemctl enable --now evsu-notifications.timer evsu-backup.timer

sudo rm -f /etc/nginx/sites-enabled/default
sudo mkdir -p /var/www/certbot
sudo cp deploy/nginx-proxy-snippet.conf /etc/nginx/snippets/evsu-proxy.conf
sudo cp deploy/nginx-public.conf /etc/nginx/sites-available/evsu-public
sudo cp deploy/nginx-staff.conf /etc/nginx/sites-available/evsu-staff
sudo nano /etc/nginx/sites-available/evsu-public   # host name, certificate paths
sudo nano /etc/nginx/sites-available/evsu-staff    # host name, certificate paths
sudo ln -s /etc/nginx/sites-available/evsu-public /etc/nginx/sites-available/evsu-staff \
    /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**Firewall.** Only web traffic and SSH are open. MySQL (3306) is never reachable from outside.
Use SSH keys and turn off password sign-in for SSH (`PasswordAuthentication no` in
`/etc/ssh/sshd_config`).

```bash
sudo ufw default deny incoming
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow OpenSSH
sudo ufw enable
```

**Certificates (Let's Encrypt, free).** Install `certbot`. Nginx will not start while a site's
certificate file is missing, so first comment out the two `listen 443 ssl` site blocks (not the
catch-all), reload Nginx, then run:

```bash
sudo certbot certonly --webroot -w /var/www/certbot -d apply.example.edu.ph
sudo certbot certonly --webroot -w /var/www/certbot -d recruit-staff.example.edu.ph
```

Restore the blocks and reload Nginx. Certbot renews certificates automatically (`systemctl list-timers` shows `certbot.timer`).

**Shared addresses.** Staff now sign in from anywhere, so set `STAFF_LOGIN_MAX_FAILURES_PER_IP` to
20 (as in `.env.production.example`). Raise it only if many staff share one network address and get
locked out together.

**Restricting the staff site later.** To make the staff site reachable from one network only (for
example the school's), add `allow <network range>; deny all;` where `nginx-staff.conf` shows it, and
reload Nginx. No application change is needed.

## 7. Verify the deployment

**Applicant site** (any internet connection, for example a phone on mobile data):

1. `https://<public host>/` loads; `http://` redirects to HTTPS. Register, verify the email link, and
   sign in as an applicant.
2. Upload a document, then download it. The response comes from Nginx (`X-Accel-Redirect` is never
   shown to the browser). Upload the harmless EICAR test file: it must be quarantined.
3. These return 404: `/admin/`, `/accounts/staff/login/`, `/recruitment/manage/vacancies/`, `/audit/`,
   and `/protected/documents/anything`.
4. A staff username and password are refused on the applicant sign-in page.
5. `https://<public IP>/` (the bare address, no host name) does not open either site.

**Staff site** (any internet connection):

6. `https://<staff host>/` shows **Staff sign in**; staff can sign in; an applicant account cannot.
   `https://<staff host>/accounts/register/` goes to the staff sign-in page.
7. As System Administrator, open **System Admin → New staff account**. The emailed link opens the
   staff site and sets the password. The first sign-in asks the person to scan a QR code with an
   authenticator app and shows ten recovery codes; later sign-ins ask for a 6-digit code after the
   password. `https://<staff host>/admin/login/` goes to the staff sign-in page.
8. Five wrong passwords for one account show the "Too many failed sign-in attempts" message, on each
   site separately.

**Both:**

9. Audit entries show the real client address, not 127.0.0.1.
10. A test email arrives through SMTP; **Email deliveries** shows no failures.
11. `systemctl list-timers` shows the backup and notification timers; after the first night,
   **Backups and restore checks** shows a completed backup. Run a restore check (RECOVERY.md).

## 8. Updating

```bash
cd /srv/evsu-recruitment
sudo -u evsu .venv/bin/python manage.py backup_create      # always back up first
sudo -u evsu git pull
sudo -u evsu .venv/bin/pip install -r requirements-linux.txt
sudo -u evsu env DB_USER=evsu_migrate DB_PASSWORD='<migration password>' \
    .venv/bin/python manage.py migrate
sudo -u evsu .venv/bin/python manage.py collectstatic --noinput
sudo systemctl reload evsu-recruitment@public evsu-recruitment@staff
```

MySQL schema changes do not roll back automatically. If a migration fails, stop, keep the backup,
and follow RECOVERY.md rather than deleting migrations.
