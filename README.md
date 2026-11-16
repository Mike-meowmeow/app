# EVSU-Ormoc Recruitment Management System

Django + MySQL web application for part-time instructor recruitment at EVSU-Ormoc HRMDO:
applicant accounts and profiles, vacancies with versioned requirements, and (in later stages)
applications, document review, the official applicant roster, committees, and analytics.

This README covers setting up a development machine. Design documents (functional requirements,
database specification, development guide) live in the separate project vault.

---

## 1. Before you start

### Software to install

| Software | Version | Notes |
| --- | --- | --- |
| Python | **3.12 or 3.13** | 3.14 works with a warning (untested). On Windows, install from [python.org](https://www.python.org/downloads/) and keep the **py launcher** option ticked. |
| Git | any recent | [git-scm.com](https://git-scm.com/downloads) |
| MySQL Server | **8.0.11 or newer** (8.4 LTS recommended) | The **server**, not only MySQL Workbench. Windows: [MySQL Installer](https://dev.mysql.com/downloads/installer/). Ubuntu: `sudo apt install mysql-server`. macOS: `brew install mysql`. |
| Build tools (macOS/Linux only) | — | Needed to compile `mysqlclient`. Ubuntu/Debian: `sudo apt install pkg-config python3-dev default-libmysqlclient-dev build-essential`. macOS: `brew install pkg-config mysql-client`. Windows needs nothing extra. |

### Information to have ready

- **The MySQL root (administrator) password** you chose when installing MySQL. Setup asks for
  it **once** to create the project database and account and to load time zone data. It is never
  saved. If you use a different administrator account, set `MYSQL_ROOT_USER` (see section 5).
- **The MySQL port.** The default is 3306. Setup detects 3306–3308 automatically; if your server
  uses another port, you will set `DB_PORT` in `.env`.
- **Internet access** for the first run (Python packages are downloaded).

### Check the MySQL server is running

- Windows: open **Services** and confirm **MySQL80** (or similar) is *Running*.
- Linux: `sudo systemctl status mysql`
- macOS: `brew services list`

---

## 2. Set up a new machine

```bash
git clone https://github.com/Mike-meowmeow/app.git
cd app
```

Then run the setup script for your system:

| System | Command |
| --- | --- |
| Windows (PowerShell) | `powershell -ExecutionPolicy Bypass -File setup.ps1` |
| macOS / Linux / Git Bash | `./setup.sh` |

The script prints numbered steps and stops with a clear **FAIL** message and a hint if something
is wrong. Fix the problem and run it again — completed steps are skipped, so it is always safe to
re-run. It:

1. Checks Python, Git, and MySQL tools.
2. Creates the virtual environment `.venv`.
3. Installs the packages in `requirements.txt`.
4. Creates `.env` from `.env.example` with a generated secret key and database password and the
   detected MySQL port. An existing `.env` is never overwritten; only missing keys are added.
5. Creates private storage folders (`private/`, `logs/`).
6. Runs Django's system checks.
7. Sets up the database (`setup_db.py`): creates the database and account if missing (asks for the
   MySQL root password), loads MySQL time zone data, applies migrations, and seeds roles and
   reference data.

A successful run ends with **Setup complete.**

---

## 3. First run

Commands below use Windows paths. On macOS/Linux replace `.\.venv\Scripts\python.exe` with
`.venv/bin/python`.

1. **Create your administrator account** (asks for a username, email, and password):
   ```powershell
   .\.venv\Scripts\python.exe manage.py createsuperuser
   ```
2. **Give yourself roles.** Superuser status alone does not open any workspace, and it never grants
   recruitment authority. Assign roles explicitly:
   ```powershell
   .\.venv\Scripts\python.exe manage.py assign_role <username> "System Administrator"
   .\.venv\Scripts\python.exe manage.py assign_role <username> "HR Personnel"
   ```
   Roles: `Applicant`, `HR Personnel`, `HR Head / Approver`, `Department Head`, `PSB Member`,
   `System Administrator`. After this, System Administrators manage other users' roles in the app
   at **/accounts/admin/users/**.
3. **Optional sample data** — a clearly labeled development department so HR can create vacancies:
   ```powershell
   .\.venv\Scripts\python.exe manage.py seed_reference_data --sample-department
   ```
   Real departments are added by a System Administrator in the Django admin (**/admin/**).
4. **Start the server:**
   ```powershell
   .\.venv\Scripts\python.exe manage.py runserver
   ```
   Open **http://127.0.0.1:8000/**. Stop it with **Ctrl+C**.

> **Emails print in the server terminal.** Development uses Django's console email backend, so
> verification and password-reset links appear in the terminal running `runserver`, not in an inbox.
> Ctrl+click the link, or copy only the `http://127.0.0.1:8000/...` URL.

---

## 4. Everyday commands

| Task | Command |
| --- | --- |
| Run the server | `.\.venv\Scripts\python.exe manage.py runserver` |
| Run all tests | `.\.venv\Scripts\python.exe manage.py test` |
| Check database readiness (no changes) | `py setup_db.py --check` |
| Apply new migrations + seed after `git pull` | `py setup_db.py` |
| Re-run the full setup | `.\setup.ps1` or `./setup.sh` |
| Assign / revoke a role | `manage.py assign_role <user> "<Role>"` (add `--revoke` to remove) |
| Seed roles and reference data | `manage.py seed_reference_data` |

On macOS/Linux use `python3 setup_db.py` instead of `py setup_db.py`.
Tests need the MySQL test database permission that setup grants; they take about a minute.
After pulling changes, run `py setup_db.py` (or the full setup if `requirements.txt` changed).

---

## 5. Configuration (`.env`)

`.env` holds machine-specific settings and secrets. It is git-ignored — **never commit it**.
`.env.example` lists every key with placeholder values.

| Key | Purpose | Default |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Django signing key (generated by setup) | — |
| `DJANGO_DEBUG` | Debug pages; `False` outside development | `True` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated host names | `localhost,127.0.0.1` |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Application database and account (password generated) | `evsu_recruitment_dev`, `evsu_dev` |
| `DB_HOST`, `DB_PORT` | MySQL server address | `127.0.0.1`, detected port |
| `SESSION_IDLE_TIMEOUT_MINUTES` | Sign-out after inactivity | `30` |
| `PASSWORD_RESET_TIMEOUT_MINUTES` | Reset link lifetime | `60` |
| `EMAIL_VERIFICATION_TIMEOUT_HOURS` | Verification link lifetime | `48` |
| `PRIVACY_NOTICE_VERSION` | Changing it makes applicants acknowledge the notice again | `DRAFT-2026-11` |
| `DEFAULT_FROM_EMAIL` | Sender address on system emails | `EVSU Recruitment <no-reply@localhost>` |

Restart the server after editing `.env`.

Setup-only environment variables (not stored in `.env`):

| Variable | Use |
| --- | --- |
| `MYSQL_ROOT_USER` | Administrator account for database setup, if not `root` |
| `MYSQL_ROOT_PASSWORD` | Supply the administrator password without a prompt (CI/scripts) |

Setup options: `setup_dev.py --skip-db`, `--recreate-venv`, `--non-interactive`;
`setup_db.py --check`, `--reset-password`, `--no-migrate`, `--non-interactive`.

---

## 6. Troubleshooting

| Problem | Fix |
| --- | --- |
| `setup.ps1 cannot be loaded because running scripts is disabled` | Run it as `powershell -ExecutionPolicy Bypass -File setup.ps1`. |
| `Python 3.12 or 3.13 was not found` | Install Python 3.13 (with the py launcher on Windows) and re-run. |
| `No MySQL server reachable at 127.0.0.1:3306` | Start the MySQL service. If the message names another port, set `DB_PORT` in `.env`. |
| `MySQL rejected the password for 'root'` | Re-run and enter the correct administrator password, or set `MYSQL_ROOT_USER`. |
| `Account 'evsu_dev' ... password differs from .env` | Put the account's real password in `DB_PASSWORD`, or run `py setup_db.py --reset-password`. |
| `mysqlclient` fails to install (macOS/Linux) | Install the build tools from section 1, then re-run setup. |
| `Are time zone definitions for your database installed?` | Run `py setup_db.py`; it loads MySQL's time zone tables (needs the root password). |
| `.venv is broken` after upgrading Python | Run setup with `--recreate-venv`. |
| Signed in but see "Access denied" / no workspaces | Your account has no role yet — use `manage.py assign_role` (section 3). |
| Can't find the verification email | Look in the terminal running `runserver` (section 3). |

---

## 7. Project layout

```text
app/
  config/         Settings, root URLs, WSGI
  core/           Shared base models, departments, recruitment types, workspaces
  accounts/       Custom user, roles (Django groups), sign-in, registration, role admin
  audit/          Protected audit trail and audit service
  documents/      Document types (file uploads arrive in Stage 5)
  recruitment/    Vacancies, requirement versions, staff assignments
  applicants/     Applicant profiles and qualification records
  templates/      Django templates (Bootstrap 5, served locally from static/vendor)
  setup.ps1 / setup.sh / setup_dev.py   Machine setup
  setup_db.py     Database provisioning, time zones, migrations, seeding
```

Business rules live in each app's `services.py` (authorization, transactions, audit) and
`selectors.py` (permission-aware queries). The Django admin is for reference data only; business
records are view-only there.

---

## 8. Current status

| Stage | Status |
| --- | --- |
| 1 Environment and skeleton | Done |
| 2 Models and migrations | Batches A–B applied; later batches arrive with their modules |
| 3 Accounts, roles, access | Done |
| 4 Vacancies and applicant profiles | Done |
| 5–11 Applications, roster, committees, notifications, staging, AI, release | Not started |

Pending institutional inputs (HR): official privacy notice wording, document checklist,
qualification reference values, approval authorities, and roster conventions. Seeded document
types and the privacy notice are development placeholders until then.
