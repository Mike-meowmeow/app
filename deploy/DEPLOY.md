# Deploying to a Linux staging or production server

This runbook sets up the EVSU-Ormoc recruitment system on Ubuntu with Nginx, Gunicorn, and MySQL.
Staging and production follow the same steps with different host names and credentials. It assumes
a server administrator with `sudo`. Paths below use `/srv/evsu-recruitment`.

The steps describe the intended setup; they have not yet been run on a staging server. Record the
versions you actually install in the project's Development Logs.

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
sudo -u evsu nano .env           # real host name, secret key, passwords, SMTP
sudo chmod 600 .env
```

`DJANGO_DEBUG=False` turns on HTTPS redirects, secure cookies, and HSTS. `PRIVATE_FILE_DELIVERY=nginx`
makes downloads go through the protected Nginx location after Django's permission check.

## 5. Migrate, seed, static files, checks

```bash
cd /srv/evsu-recruitment
sudo -u evsu env DB_USER=evsu_migrate DB_PASSWORD='<migration password>' \
    .venv/bin/python manage.py migrate
sudo -u evsu .venv/bin/python manage.py seed_reference_data
sudo -u evsu .venv/bin/python manage.py collectstatic --noinput
sudo -u evsu .venv/bin/python manage.py check --deploy
sudo -u evsu .venv/bin/python -m pip check
```

`check --deploy` must report no issues. Then create the first administrator and register the
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
sudo systemctl enable --now evsu-recruitment.service
sudo systemctl enable --now evsu-notifications.timer evsu-backup.timer

sudo cp deploy/nginx-evsu-recruitment.conf /etc/nginx/sites-available/evsu-recruitment
sudo ln -s /etc/nginx/sites-available/evsu-recruitment /etc/nginx/sites-enabled/
sudo nano /etc/nginx/sites-available/evsu-recruitment   # host name and certificate paths
sudo nginx -t && sudo systemctl reload nginx
```

Use a certificate from the institution or Let's Encrypt (`certbot --nginx`).

## 7. Verify the deployment

1. `https://<host>/` loads; `http://` redirects to HTTPS.
2. `https://<host>/protected/documents/anything` returns 404 (internal location only).
3. Sign in as an applicant and download your own document; the response comes from Nginx
   (`X-Accel-Redirect` is never shown to the browser).
4. Five wrong passwords for one account show the "Too many failed sign-in attempts" message.
5. Audit entries show the real client address, not 127.0.0.1.
6. A test email arrives through SMTP; **Email deliveries** shows no failures.
7. `systemctl list-timers` shows the backup and notification timers; after the first night,
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
sudo -u evsu .venv/bin/python manage.py check --deploy
sudo systemctl reload evsu-recruitment
```

MySQL schema changes do not roll back automatically. If a migration fails, stop, keep the backup,
and follow RECOVERY.md rather than deleting migrations.
