# Deploying to a Linux staging or production server

This runbook sets up the EVSU-Ormoc recruitment system on Ubuntu with Nginx, Gunicorn, and MySQL.
Staging and production follow the same steps with different host names and credentials. It assumes
a server administrator with `sudo`. Paths below use `/srv/evsu-recruitment`.

The steps describe the intended setup; they have not yet been run on a server. Record the versions
you actually install in the project's Development Logs.

## Layout: one server at school, two sites

| | Public applicant site | Staff site |
| --- | --- | --- |
| Example address | `https://apply.example.edu.ph` | `https://recruit-staff.example.edu.ph` |
| Reachable from | The internet | The school network only |
| Service | `evsu-recruitment@public` | `evsu-recruitment@staff` |
| Nginx ports on this server | 8080, 8443 | school-network address, 80 and 443 |
| Accounts | Applicants only | Staff only |

Both sites use the same code, MySQL database, and private files, so there is nothing to synchronize.

**You need from school IT before starting:** a fixed public IP (or a tunnel), a public DNS name for
the applicant site, an internal DNS name for the staff site, a fixed school-network address for the
server, the school network range, and router access for the two port forwards in section 6. The
school's Data Privacy Officer should review the public site before launch.

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
sudo nano /etc/nginx/sites-available/evsu-staff    # addresses, network range, host name, certificate
sudo ln -s /etc/nginx/sites-available/evsu-public /etc/nginx/sites-available/evsu-staff \
    /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

**Router (only these two forwards):**

| Internet port | Goes to this server's port |
| --- | --- |
| 80 | 8080 |
| 443 | 8443 |

Never forward this server's ports 80, 443, 22, or 3306. If IT uses a tunnel instead of port
forwarding, point it only at the public site on port 8443.

**Firewall** (replace the range with the school network):

```bash
sudo ufw default deny incoming
sudo ufw allow 8080/tcp
sudo ufw allow 8443/tcp
sudo ufw allow from 192.0.2.0/24 to any port 80,443 proto tcp
sudo ufw allow from 192.0.2.0/24 to any port 22 proto tcp
sudo ufw enable
```

**Certificates.** Public site: temporarily comment out its `listen 8443` server blocks if Nginx will
not start without the certificate, then run
`sudo certbot certonly --webroot -w /var/www/certbot -d apply.example.edu.ph`, restore the blocks, and
reload Nginx. Staff site: use a certificate from the institution, or a Let's Encrypt DNS challenge
for a real subdomain that resolves only inside the school.

If the router rewrites the source address of forwarded traffic, every applicant appears to come from
the router, which breaks per-address sign-in limits and audit addresses. Ask IT to preserve it.

## 7. Verify the deployment

**From outside the school** (a phone on mobile data, Wi-Fi off):

1. `https://<public host>/` loads; `http://` redirects to HTTPS. Register, verify the email link, and
   sign in as an applicant.
2. Upload a document, then download it. The response comes from Nginx (`X-Accel-Redirect` is never
   shown to the browser). Upload the harmless EICAR test file: it must be quarantined.
3. These return 404: `/admin/`, `/accounts/staff/login/`, `/recruitment/manage/vacancies/`, `/audit/`,
   and `/protected/documents/anything`.
4. A staff username and password are refused on the applicant sign-in page.
5. The staff host name does not open. Also confirm with
   `curl -k --resolve <staff host>:443:<public IP> https://<staff host>/`, which must not return the
   staff sign-in page.

**From a computer on the school network:**

6. `https://<staff host>/` shows **Staff sign in**; staff can sign in; an applicant account cannot.
7. As System Administrator, open **System Admin → New staff account**. The emailed link opens the
   staff site and sets the password.
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
