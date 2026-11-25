# EVSU-Ormoc Recruitment Management System

Django + MySQL web application for part-time instructor recruitment at EVSU-Ormoc HRMDO:
applicant accounts and profiles, a reusable position catalog, vacancies with versioned
requirements, applications, document review, the official applicant roster, committees and
interviews, notifications, analytics, and backup/restore tools. Applicants and staff use separate
accounts and sites. AI features are not built yet.

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
- **The official roster form** `MATRIX_TEACHING APPLICANTS FOR PSB.xlsx` (PART-TIME worksheet),
  obtained from HRMDO. It is **not** stored in the repository. Without it you can register a
  synthetic development template instead (section 3, step 4).

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
   Staff roles: `HR Personnel`, `HR Head / Approver`, `Department Head`, `PSB Member`,
   `System Administrator`. One staff account may hold several. `Applicant` cannot be combined with
   a staff role; applicants register themselves. After this, System Administrators create staff
   accounts and manage their roles in the app at **/accounts/admin/users/**.
3. **Optional sample data** — a clearly labeled development department so HR can create vacancies:
   ```powershell
   .\.venv\Scripts\python.exe manage.py seed_reference_data --sample-department
   ```
   Real departments are added by a System Administrator in the Django admin (**/admin/**).
   HR then builds the **Position catalog** (HR workspace → Position catalog, or Vacancies →
   Position catalog): each position's title, default description and default requirements.
   New vacancies are created by choosing a position; its defaults are copied into the vacancy,
   which HR adjusts before publishing. Catalog edits never change existing vacancies. Publishing
   a vacancy also creates its committee with a standard name; HR assigns the members under
   **Committees & interviews**.
4. **Register the official roster template** (needed before HR can prepare rosters). The file is
   checked against the PART-TIME layout, copied into `private/templates/`, and recorded with its
   SHA-256; `--approve-as` must name an HR Head / Approver or System Administrator:
   ```powershell
   .\.venv\Scripts\python.exe manage.py register_roster_template "C:\path\to\MATRIX_TEACHING APPLICANTS FOR PSB.xlsx" --approve-as <username>
   ```
   No official file yet? Use `register_roster_template --sample --approve-as <username>` for a
   clearly labeled development template. Set the default signatories afterwards at
   **/rosters/templates/** (they are not copied from the file).
5. **Start the server:**
   ```powershell
   .\.venv\Scripts\python.exe manage.py runserver
   ```
   Open **http://127.0.0.1:8000/**. Staff sign in at **/accounts/staff/login/** and applicants at
   **/accounts/login/**. Stop the server with **Ctrl+C**. To run the applicant and staff sites
   separately, as in production, see section 5b.

> **Emails print in the server terminal.** Development uses Django's console email backend, so
> verification and password-reset links appear in the terminal running `runserver`, not in an inbox.
> Ctrl+click the link, or copy only the `http://127.0.0.1:8000/...` URL.

---

## 4. Everyday commands

| Task | Command |
| --- | --- |
| Run the server | `.\.venv\Scripts\python.exe manage.py runserver` |
| Run the applicant and staff sites at the same time | Two terminals, ports 8000 and 8001 (section 5b) |
| Run all tests | `.\.venv\Scripts\python.exe manage.py test --noinput` |
| Check database readiness (no changes) | `py setup_db.py --check` |
| Apply new migrations + seed after `git pull` | `py setup_db.py` |
| Re-run the full setup | `.\setup.ps1` or `./setup.sh` |
| Assign / revoke a role | `manage.py assign_role <user> "<Role>"` (add `--revoke` to remove) |
| Seed roles and reference data | `manage.py seed_reference_data` |
| Register a roster template | `manage.py register_roster_template <file.xlsx> --approve-as <user>` |
| Send owed notification emails (retry) | `manage.py send_notifications` |
| Create a backup set (database + private files) | `manage.py backup_create` |
| Check stored backups are intact | `manage.py backup_verify` |
| Restore the latest backup into an isolated database and verify it | `manage.py restore_check --requested-by <admin>` |
| List (or `--delete`) unreferenced private files | `manage.py sweep_orphan_files` |

On macOS/Linux use `python3 setup_db.py` instead of `py setup_db.py`.
Tests need the MySQL test database permission that setup grants; the full suite takes about 10
minutes. Run them in a terminal where `SITE_PORTAL` is not set.
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
| `CORRECTION_WINDOW_DAYS` | Default deadline HR is offered for correction requests | `5` |
| `MALWARE_SCAN_COMMAND` | Optional scanner (e.g. `clamscan --no-summary`); exit 0 clean, 1 infected | empty = "not scanned" |
| `ROSTER_ALLOW_SELF_APPROVAL` | Whether the HR preparer may also approve their own roster | `False` |
| `EMAIL_BACKEND`, `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD`, `EMAIL_USE_TLS` | Email delivery; the console backend prints emails in the server terminal | console backend |
| `NOTIFICATION_EMAIL_ENABLED`, `NOTIFICATION_MAX_ATTEMPTS` | Email copies of in-app notifications and how many attempts are made | `True`, `3` |
| `SITE_PORTAL` | Site this process serves: `public`, `staff`, or `all` (development only) | `all` |
| `SITE_URL` | Base address for email links in development | `http://127.0.0.1:8000` |
| `PUBLIC_SITE_URL`, `STAFF_SITE_URL` | Addresses used in applicant and staff email links | `SITE_URL` |
| `STAFF_LOGIN_MAX_FAILURES_PER_IP` | Failed staff sign-ins allowed per address | `100` |
| `STAFF_TWO_FACTOR_REQUIRED`, `TWO_FACTOR_ISSUER` | Require an authenticator app code at every staff sign-in; the name shown in the app | on when `DJANGO_DEBUG=False`, `EVSU Recruitment` |
| `MALWARE_SCAN_REQUIRED` | Refuse uploads that could not be scanned | on for the production public site |
| `BACKUP_ROOT`, `MYSQL_BIN_DIR`, `BACKUP_MAX_AGE_HOURS` | Where backup sets go, where `mysqldump`/`mysql` are (if not found automatically), and when the last backup counts as overdue | `backups/`, auto, `26` |
| `LOGIN_MAX_FAILURES`, `LOGIN_MAX_FAILURES_PER_IP`, `LOGIN_LOCKOUT_MINUTES` | Sign-in throttling per account identifier and per address | `5`, `20`, `15` |
| `TRUSTED_PROXIES`, `PRIVATE_FILE_DELIVERY` | Proxy addresses whose X-Forwarded-For is trusted; `nginx` to deliver private files through X-Accel-Redirect | empty, `django` |

Uploaded documents (`private/documents/`), roster templates (`private/templates/`), generated
roster files (`private/exports/`), and generated reports (`private/reports/`) are git-ignored and
only served through permission-checked download links. Back up the whole `private/` folder
together with the database.

Restart the server after editing `.env`.

Setup-only environment variables (not stored in `.env`):

| Variable | Use |
| --- | --- |
| `MYSQL_ROOT_USER` | Administrator account for database setup, if not `root` |
| `MYSQL_ROOT_PASSWORD` | Supply the administrator password without a prompt (CI/scripts) |

Setup options: `setup_dev.py --skip-db`, `--recreate-venv`, `--non-interactive`;
`setup_db.py --check`, `--reset-password`, `--no-migrate`, `--non-interactive`.

---

## 5a. Backups and deployment

- **Backups:** `manage.py backup_create` writes one consistent set (database dump, then documents,
  roster files, reports, and templates) to `backups/`, which is git-ignored. Check old sets with
  `backup_verify`. Run `restore_check --requested-by <admin>` regularly; it restores into the
  isolated `<DB_NAME>_restore` database, never the live one. System Administrators see the results
  under the account menu, **Backups and restore checks**. The recovery procedure is in
  `deploy/RECOVERY.md`.
- **Releases:** `deploy/RELEASE.md` is the release checklist (tests, backup and restore check,
  migrate, smoke test), the **upgrade check** that upgrades a database filled by the previous
  version and compares it (`deploy/upgrade-check/`), and the rollback plan. AI features can be
  switched off in `.env` without a rollback.
- **Internet server:** follow `deploy/DEPLOY.md` (two sites on one server, Nginx, Gunicorn,
  Let's Encrypt certificates, firewall, systemd timers for backups and email retries). These steps have not yet been run on a real
  server. Production settings are in `.env.production.example` and `deploy/*.env.example`. With
  `DJANGO_DEBUG=False`, HTTPS, secure cookies, and HSTS turn on; each site must pass
  `manage.py check --deploy`.
- **AI server:** `deploy/AI-SERVER.md` sets up one headless Ubuntu server for the sites, MySQL, and
  the AI runtime (OCR, spaCy, SBERT); `deploy/SERVER-INSTALL.md` walks a first-timer through the
  Ubuntu installation; `deploy/ai_check.py` proves the runtime works; `deploy/DEV-SERVER.md`
  explains everyday use of the development copy running on that server.
- **AI extraction (Stage 10, advisory):** after HR verifies a document, the `ai_worker` service
  reads it (digital PDF text, or OCR for scans and images) and proposes education, experience,
  training, eligibility, and license items. HR accepts, corrects (with a reason), links to an
  existing fact, or rejects each one on the extraction page; the original proposal is kept, and AI
  never changes statuses, shortlists, decisions, or rosters. Off unless `AI_EXTRACTION_ENABLED=True`;
  failures leave manual review untouched. `manage.py make_ai_samples` writes made-up test documents,
  and `manage.py ai_extract_file` shows what would be proposed for one file. The AI packages
  (`requirements-ai.txt`) are installed only on the server.
- **Qualification checks (Stage 10, advisory):** on an application's Qualification review, HR
  presses **Run qualification checks**. Fixed rules compare the accepted facts with each published
  standard and suggest Meets / Does Not Meet / Needs Review / Not Required, with the reasoning and
  the facts used (overlapping work counted once, relevance required, incomplete degrees and missing
  hours sent to HR). HR records the official outcome, optionally starting from the suggestion. No
  AI packages needed; `AI_QUALIFICATION_CHECKS_ENABLED` (on by default) turns it off.
- **Competency matching (Stage 10, advisory):** **Run competency matching** on an application
  queues an AI worker job that compares each vacancy competency with the accepted qualification
  wording using SBERT (`SBERT_MODEL`) and shows the cosine similarity (-1 to 1) of every pair, with
  the model revision. Not a score or ranking; names, contact details, schools, and employers are
  never inputs. Off unless `AI_COMPETENCY_MATCHING_ENABLED=True` on a computer with the model.
  `manage.py evaluate_competency_matching pairs.csv` compares the model with HR's judgments.
- **AI review summary:** the HR workspace's **AI extraction results** page (and
  `manage.py ai_review_report`) shows per document type how many proposed items HR accepted,
  corrected, linked, or rejected, with the fields corrected most. `ai_processing/test_acceptance.py`
  runs every AI feature end to end with AI on.
- **Audit trail:** HR Head / Approver and System Administrator can search and export it from the
  account menu, **Audit trail**.

## 5b. Applicant and staff sites

Applicants and staff use separate accounts and separate sign-in pages. In production they are two
sites on one internet server, each with its own address and no network restriction
(`deploy/DEPLOY.md`). Staff accounts are created by a System Administrator at
**System Admin → New staff account**; the person sets a password through an emailed link.

Staff sign-in has a second step: a 6-digit code from an authenticator app (Google Authenticator,
Microsoft Authenticator, or similar). It is required in production; in development it is off
unless `STAFF_TWO_FACTOR_REQUIRED=True`, but a staff member can turn it on at **My account → Set
up two-factor sign-in**. The first sign-in shows a QR code to scan and ten single-use recovery
codes. A System Administrator can reset it for someone who lost their phone, from that person's
page under **System Admin**.

### Option 1: one server (everyday development)

A plain `runserver` serves every page on one address:

```powershell
cd "C:\path\to\app"   # your app folder
.\.venv\Scripts\python.exe manage.py runserver
```

Open **http://127.0.0.1:8000/**. Applicants sign in at `/accounts/login/` and staff at
`/accounts/staff/login/`.

### Option 2: applicant and staff sites running at the same time

This works like the real deployment. You need **two terminals**, because each server keeps its
terminal busy. In VS Code, open a second terminal with the **+** button in the terminal panel.

**Terminal 1: applicant site (port 8000)**

```powershell
cd "C:\path\to\app"
$env:SITE_PORTAL="public"; $env:PUBLIC_SITE_URL="http://127.0.0.1:8000"; $env:STAFF_SITE_URL="http://127.0.0.1:8001"
.\.venv\Scripts\python.exe manage.py runserver 8000
```

**Terminal 2: staff site (port 8001)**

```powershell
cd "C:\path\to\app"
$env:SITE_PORTAL="staff"; $env:PUBLIC_SITE_URL="http://127.0.0.1:8000"; $env:STAFF_SITE_URL="http://127.0.0.1:8001"
.\.venv\Scripts\python.exe manage.py runserver 8001
```

Replace the `cd` path with your own `app` folder. Each terminal should end with
`Starting development server at http://127.0.0.1:8000/` (or `:8001/`) and keep running.

| Site | Address | Who signs in |
| --- | --- | --- |
| Applicant | http://127.0.0.1:8000/ | Applicant accounts only; registration is here |
| Staff | http://127.0.0.1:8001/ | Staff accounts only (opens **Staff sign in**) |

- The two sites share one database, so an application submitted on 8000 appears on 8001 at once.
- Each site has its own sign-in, so you can stay signed in to both in the same browser.
- Emails, such as verification, password reset, and staff invitation links, print in the terminal of
  the site that sent them, and each link points to the correct site.
- On the applicant site, staff pages such as `/admin/` return "Page not found". In development this
  is Django's yellow debug page.

**macOS/Linux:** use `export` and `.venv/bin/python`, for example:

```bash
SITE_PORTAL=public PUBLIC_SITE_URL=http://127.0.0.1:8000 STAFF_SITE_URL=http://127.0.0.1:8001 .venv/bin/python manage.py runserver 8000
SITE_PORTAL=staff  PUBLIC_SITE_URL=http://127.0.0.1:8000 STAFF_SITE_URL=http://127.0.0.1:8001 .venv/bin/python manage.py runserver 8001
```

**Stopping:** press **Ctrl+C** in each terminal, then close both terminals. The `$env:` settings
stay active in a PowerShell terminal until it is closed, so do not reuse those terminals for
Option 1 or for running tests. To clear them instead, run
`Remove-Item Env:SITE_PORTAL, Env:PUBLIC_SITE_URL, Env:STAFF_SITE_URL`.

Common problems: `.venv\Scripts\python.exe is not recognized` means the terminal is not in the
`app` folder; `That port is already in use` means another server is still running on that port.

Pages show breadcrumbs. Staff trails include the workspace, such as **Home > HR > Vacancies**. With
several roles, the trail follows the workspace opened last when it covers the page.

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
| `restore_check` says it cannot use `..._restore`, or `setup_db.py --check` reports the restore-check database | Run `py setup_db.py` once and enter the MySQL root password; it grants the app account the isolated restore-check database. |
| "Too many failed sign-in attempts" | Wait `LOGIN_LOCKOUT_MINUTES` (15) or reset the password. Each site counts failures separately. |
| Correct staff password is refused | Staff must use `/accounts/staff/login/`; applicants use `/accounts/login/`. |
| `.\.venv\Scripts\python.exe` is not recognized | Change to the `app` folder first. |
| `That port is already in use` | Stop the other `runserver` with Ctrl+C or close its terminal. |
| `navigation is not a registered tag library` | Restart `runserver` after pulling these changes. |
| Many tests fail with 404 or redirects | Use a terminal where `SITE_PORTAL` is not set. |

---

## 7. Project layout

```text
app/
  config/         Settings, root URLs, WSGI
  core/           Shared base models, departments, recruitment types, workspaces, breadcrumbs
  accounts/       Custom user, roles, applicant and staff sign-in, site separation, staff accounts
  audit/          Protected audit trail and audit service
  documents/      Document types, private versioned uploads, verification, downloads
  recruitment/    Position catalog, vacancies, requirement versions, staff assignments, applications,
                  snapshots, facts, corrections, workflow, human decisions and release
  applicants/     Applicant profiles and qualification records
  matrix/         Official applicant roster: templates, versions, approval, Excel/PDF export
  committee/      Automatically named committees, conflicts, interviews, narrative evaluations
  notifications/  In-app notifications and email delivery attempts (retry with send_notifications)
  analytics/      Recruitment metrics, snapshots, Excel/PDF reports
  operations/     Backup sets, restore checks, monitoring; orphan-file sweep
  deploy/         Linux server files: Nginx, Gunicorn, systemd units, DEPLOY.md, RECOVERY.md,
                  RELEASE.md, upgrade-check/
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
| 2 Models and migrations | All non-AI batches (A–F) applied; AI tables arrive in Stage 10 |
| 3 Accounts, roles, access | Done |
| 4 Vacancies and applicant profiles | Done; position catalog added 18 Nov 2026 |
| 5 Applications, documents, manual review | Done |
| 6 Official roster, approval, Excel/PDF | Done |
| 7 Committees, interviews, decisions | Done |
| 8 Notifications, search, analytics | Done |
| 9 Stabilization: backup/restore, production settings, two-site deployment files | Code done; school server deployment and HR walkthrough pending |
| 10–11 AI features, final release | Not started |

Pending institutional inputs (HR): official privacy notice wording, document checklist,
qualification reference values, approval authorities (including self-approval of rosters,
shortlisting, final decisions, decision release, and conflict resolution), and
roster conventions (row order, missing-value labels, remark wording, signatories, treatment of
unverified values at release). Seeded document types and the privacy notice are development
placeholders until then.

Before the public launch: a server with a public address, a domain with the two site names,
certificates, an SMTP account, Data Privacy Officer review, and an HR rule for deadlines
after an internet or power outage.
