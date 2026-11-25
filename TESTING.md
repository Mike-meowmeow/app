# Testing guide: applicant and staff sites

This guide walks through testing the system on your own computer. It covers the latest features:

- separate applicant and staff sign-in and sites;
- staff accounts created by a System Administrator;
- the position catalog;
- automatic committee creation and naming;
- breadcrumb navigation;
- two-factor sign-in for staff.

It takes about 1 to 1.5 hours. Do the parts in order, because later parts use the accounts and
records you create earlier. Record each result in the checklist at the end.

Commands are for Windows PowerShell, run from the `app` folder. On macOS/Linux, replace
`.\.venv\Scripts\python.exe` with `.venv/bin/python` and `py` with `python3`.

---

## Part 0. Get the latest code

**If the project is not set up on your computer yet**, follow README sections 1 to 3 first. You
will need your MySQL root password.

**If you already have the project:**

```powershell
cd "C:\path\to\app"
git pull
py setup_db.py
```

`setup_db.py` applies the new database migration (`recruitment.0004_position_catalog`). Enter your
MySQL root password if it asks.

Confirm you have the new code: `git log --oneline -3` should show the latest commit, and the file
`accounts/portal.py` should exist. If it does not, the new work has not been pushed yet. Ask
before continuing.

---

## Part 1. Automated tests (about 10 minutes)

Open a fresh terminal in the `app` folder and run:

```powershell
.\.venv\Scripts\python.exe manage.py test --noinput
```

**Expected:** a final line `OK`, after about 294 tests. Write down the number shown.

If many tests fail with 404 errors or redirects, that terminal has `SITE_PORTAL` set; open a new
one. For any other failure, copy the whole failure text into your report.

---

## Part 2. Prepare test accounts and data

1. **Create your administrator account** (choose your own username, email, and password):
   ```powershell
   .\.venv\Scripts\python.exe manage.py createsuperuser
   ```
2. **Give it staff roles.** Use the username you just created in place of `<you>`:
   ```powershell
   .\.venv\Scripts\python.exe manage.py assign_role <you> "System Administrator"
   .\.venv\Scripts\python.exe manage.py assign_role <you> "HR Personnel"
   .\.venv\Scripts\python.exe manage.py assign_role <you> "HR Head / Approver"
   ```
3. **Add a sample department and a sample roster template:**
   ```powershell
   .\.venv\Scripts\python.exe manage.py seed_reference_data --sample-department
   .\.venv\Scripts\python.exe manage.py register_roster_template --sample --approve-as <you>
   ```
4. **Check the separation rule from the command line:**
   ```powershell
   .\.venv\Scripts\python.exe manage.py assign_role <you> "Applicant"
   ```
   **Expected:** an error saying an account cannot be both an applicant and staff.

Have any small PDF file ready for uploading in Part 7.

---

## Part 3. Start both sites

Open **two terminals**. In VS Code, use the **+** button in the terminal panel.

**Terminal 1, applicant site:**
```powershell
cd "C:\path\to\app"
$env:SITE_PORTAL="public"; $env:PUBLIC_SITE_URL="http://127.0.0.1:8000"; $env:STAFF_SITE_URL="http://127.0.0.1:8001"
.\.venv\Scripts\python.exe manage.py runserver 8000
```

**Terminal 2, staff site:**
```powershell
cd "C:\path\to\app"
$env:SITE_PORTAL="staff"; $env:PUBLIC_SITE_URL="http://127.0.0.1:8000"; $env:STAFF_SITE_URL="http://127.0.0.1:8001"
.\.venv\Scripts\python.exe manage.py runserver 8001
```

**Expected:** each terminal shows `Starting development server at ...` and keeps running.

- Applicant site: **http://127.0.0.1:8000/**
- Staff site: **http://127.0.0.1:8001/**

Emails are not really sent. They print in the terminal of the site that sent them. Look there for
verification and set-password links.

---

## Part 4. Which pages each site shows

Open each address and compare with the expected result.

| # | Address | Expected |
| --- | --- | --- |
| 4.1 | `http://127.0.0.1:8000/` | Applicant home page, with no mention of staff |
| 4.2 | `http://127.0.0.1:8000/accounts/login/` | **Applicant sign in** |
| 4.3 | `http://127.0.0.1:8000/accounts/staff/login/` | Page not found (yellow debug page in development) |
| 4.4 | `http://127.0.0.1:8000/admin/` | Page not found |
| 4.5 | `http://127.0.0.1:8000/recruitment/manage/vacancies/` | Page not found |
| 4.6 | `http://127.0.0.1:8001/` | Goes to **Staff sign in** |
| 4.7 | `http://127.0.0.1:8001/accounts/register/` | Goes to **Staff sign in** |

---

## Part 5. Staff sign-in and staff accounts

| # | Step | Expected |
| --- | --- | --- |
| 5.1 | On 8001, sign in with your administrator account | Signed in; a page asking which workspace to open, because you have several roles |
| 5.2 | On 8000, sign in with the same account | "Incorrect username/email or password." |
| 5.3 | On 8001, open **System Admin**, then **New staff account**. Username `hrtest`, email `hrtest@example.com`, role **HR Personnel**, any reason | Account created; Terminal 2 prints an email with a link starting `http://127.0.0.1:8001/` |
| 5.4 | Repeat for `psbtest`, email `psbtest@example.com`, role **PSB Member** | Same as above |
| 5.5 | Sign out, open the `hrtest` link, set a password | Password set |
| 5.6 | Sign in as `hrtest` on 8001 | Signed in; goes straight to the HR workspace |
| 5.7 | Set `psbtest`'s password the same way | Password set |
| 5.8 | Open the New staff account form again | **Applicant** is not offered as a role |

---

## Part 6. Position catalog, vacancy, and automatic committee

Use the staff site (8001), signed in as `hrtest` or your administrator account.

| # | Step | Expected |
| --- | --- | --- |
| 6.1 | **HR → Position catalog → New position**: title `Part-Time Instructor - Test`, any description | Position created |
| 6.2 | Add defaults: document **Personal Data Sheet** (required), a qualification standard (Education, Master's), and a competency | All three listed |
| 6.3 | **Vacancies → New vacancy**: choose the position | Description fills in; a preview lists the default requirements |
| 6.4 | Edit the description, then click **Reload default description** | The page asks before replacing your edited text |
| 6.5 | Choose the sample department, set opening time to now and deadline to next week, then **Create draft** | Draft vacancy with the requirements copied from the position |
| 6.6 | **Publish vacancy** | "Vacancy published and its committee created." |
| 6.7 | Open **Committees & interviews** | One committee named `PSB - Part-Time Instructor - Test - VAC-...`; there is no name field |
| 6.8 | Add `psbtest` as a member | Member listed |
| 6.9 | Edit the catalog position's description | The vacancy you created does **not** change |

---

## Part 7. Applicant registration and application

| # | Step | Expected |
| --- | --- | --- |
| 7.1 | On 8000, **Register** with username `appltest`, email `appltest@example.com` | "Check your email"; Terminal 1 prints a verification link starting `http://127.0.0.1:8000/` |
| 7.2 | Open the link, then sign in on 8000 | Signed in; privacy notice may ask for acknowledgment |
| 7.3 | Try `appltest` on the staff site (8001) | "Incorrect username/email or password." |
| 7.4 | **My profile**: fill in personal information and add one education record | Saved |
| 7.5 | Open **Open vacancies**, choose the vacancy from Part 6, apply, upload the PDF as the Personal Data Sheet, and submit | Application submitted |
| 7.6 | Confirm the top menu has no **Vacancies** link while signed in | Not shown (it appears only when signed out) |

---

## Part 8. Applicant and staff working together

| # | Step | Expected |
| --- | --- | --- |
| 8.1 | On 8001, open the vacancy, then **Applications** | `appltest`'s application is listed (same database) |
| 8.2 | Open it, click **Start review**, then **Request correction** (choose the document, write a message, keep the deadline) | Terminal **2** prints an email whose link starts with `http://127.0.0.1:8000/` |
| 8.3 | On 8000, as `appltest`, open **Notifications** | The correction request appears |

---

## Part 9. Breadcrumbs

| # | Where | Expected breadcrumb |
| --- | --- | --- |
| 9.1 | 8000, `appltest`'s home | Home |
| 9.2 | 8000, the application page | Home › My applications › APP-... |
| 9.3 | 8001, `hrtest` on Vacancies | Home › HR › Vacancies |
| 9.4 | 8001, `hrtest` on a vacancy's Applications page | Home › HR › Vacancies › *position* › Applications |
| 9.5 | 8001, `psbtest` on Assigned candidates | Home › PSB › Assigned candidates |
| 9.6 | 8001, administrator: open the **Approver** workspace, then Vacancies | Home › Approver › Vacancies |
| 9.7 | 8001, administrator: open **System Admin**, then New staff account | Home › System Admin › New staff account |
| 9.8 | Click every link in a breadcrumb | Each opens without an error |

---

## Part 10. Password reset, sign-in limits, and two-factor sign-in

| # | Step | Expected |
| --- | --- | --- |
| 10.1 | 8000, **Forgot password?**, enter `hrtest@example.com` | Normal "check your email" page, but **nothing** prints in Terminal 1 |
| 10.2 | 8000, same with `appltest@example.com` | A link to `:8000` prints in Terminal 1 |
| 10.3 | 8001, **Forgot password?**, enter `appltest@example.com` | Nothing prints in Terminal 2 |
| 10.4 | 8000, sign in as `psbtest` with a wrong password five times, then once more | "Too many failed sign-in attempts" |
| 10.5 | 8001, sign in as `psbtest` with the correct password | **Works**: failures on the applicant site do not lock staff out |
| 10.6 | Open the staff sign-in page in two tabs, sign in on the first, then submit the second | Goes to the dashboard; no "403 Forbidden" error |

Steps 10.7 to 10.11 need an authenticator app on your phone (Google Authenticator, Microsoft
Authenticator, or similar). Two-factor sign-in is required on the real server; in development you
turn it on yourself.

| # | Step | Expected |
| --- | --- | --- |
| 10.7 | 8001, as `hrtest`: **My account → Set up two-factor sign-in**. Scan the QR code, enter the 6-digit code | "Two-factor sign-in is on" and ten recovery codes. Write one down |
| 10.8 | Sign out, sign in as `hrtest` with the password | A page asks for the authentication code; you are not signed in yet |
| 10.9 | Enter `000000` | "That code is not correct"; still not signed in |
| 10.10 | Enter the code the app shows now | Signed in. Sign out, sign in again, and use the recovery code from 10.7: works once, with a message saying how many are left |
| 10.11 | As your administrator: **System Admin**, open `hrtest`, **Reset two-factor sign-in** with a reason. Then sign in as `hrtest` | Reset message; `hrtest` signs in with only the password again (in development) |

If you lock yourself out, wait 15 minutes.

---

## Part 11. Backups

Stop both servers (Ctrl+C), close those terminals, and open a new one in `app`:

```powershell
.\.venv\Scripts\python.exe manage.py backup_create --requested-by <you>
.\.venv\Scripts\python.exe manage.py restore_check --requested-by <you>
```

**Expected:** `Backup ... completed`, then every check marked `PASS` and `Restore check of ... passed.`
If the restore check says it cannot use the `_restore` database, run `py setup_db.py` once with the
MySQL root password, then try again.

---

## Results checklist

Copy this table into your report and fill in **Pass** or **Fail** for each row.

| Part | Result | Notes |
| --- | --- | --- |
| 1 Automated tests (number of tests: ___) | | |
| 2 Command-line separation check | | |
| 4 Pages per site (4.1-4.7) | | |
| 5 Staff sign-in and accounts (5.1-5.8) | | |
| 6 Position catalog, vacancy, committee (6.1-6.9) | | |
| 7 Applicant registration and application (7.1-7.6) | | |
| 8 Applicant and staff together (8.1-8.3) | | |
| 9 Breadcrumbs (9.1-9.8) | | |
| 10 Password reset, limits, two-factor (10.1-10.11) | | |
| 11 Backups | | |

**For each failure, include:**
- the step number;
- what you did;
- what you expected and what happened instead;
- a screenshot;
- the last lines of the terminal output.

Never include passwords or your `.env` file.

**Clean-up (optional):** test accounts can be disabled at **System Admin → Manage**, by unticking
**Account active**.
