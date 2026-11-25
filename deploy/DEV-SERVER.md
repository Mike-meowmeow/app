# Using the development copy on the AI server

The AI server runs a **development copy** of the whole system, with the AI turned on, so the team
can test real OCR and AI features before the public deployment. This guide covers everyday use:
connecting, accounts, services, logs, email, updating the code, and fixing common problems.

How the server was set up is in `deploy/SERVER-INSTALL.md` and `deploy/AI-SERVER.md`. The public
deployment (HTTPS, two sites, a domain) is `deploy/DEPLOY.md` and is not done yet.

**Use only made-up test data here** (`manage.py make_ai_samples`). Real applicant data stays off this
machine until the Data Privacy Officer approves.

---

## 1. What runs where

```
Your laptop (Windows)                         AI server "evsu-server" (Ubuntu 24.04)
 - VS Code, code changes                       192.168.1.107, home network only
 - its own development copy                    - evsu-dev          website, port 8000
   (127.0.0.1:8000, MySQL port 3308,           - evsu-ai-worker    reads verified documents
   AI off)                                     - evsu-notifications.timer  email retries
 - browser ---- http://192.168.1.107:8000 ---> - mysql              database evsu_recruitment_dev
 - ssh -------------------------------------->   (separate from the laptop's database)
                                               - /srv/evsu-recruitment       code (git) and .env
        both get the code from GitHub          - /srv/evsu-recruitment/private  uploaded files
                                               - /srv/evsu-models            AI models
```

| Item | Value |
| --- | --- |
| Website | `http://192.168.1.107:8000` (staff sign-in: `/accounts/staff/login/`) |
| Reachable from | The home network (192.168.1.x) only; the firewall blocks port 8000 elsewhere |
| Server sign-in | `ssh admin-mike@192.168.1.107` with your SSH key (passwords are refused) |
| Project folder | `/srv/evsu-recruitment`, owned by the service account `evsu` |
| Settings | `/srv/evsu-recruitment/.env` (secret; never copy it off the server or commit it) |
| Mode | `DJANGO_DEBUG=True`: plain HTTP, one site for applicants and staff, two-factor optional |

The laptop and the server have **separate databases**: an account or application created on one does
not exist on the other.

---

## 2. Connecting

**(your laptop, PowerShell)**

```powershell
ssh admin-mike@192.168.1.107          # opens the server's terminal; type exit to leave
```

If you are asked for the key's passphrase every time, load it once per Windows session:
`ssh-add $env:USERPROFILE\.ssh\id_ed25519`.

The project folder belongs to the service account `evsu`. Your account is a member of the `evsu`
group, so it can enter the folder (the `.env` file stays readable by `evsu` only). For another
administrator account, add it once with `sudo usermod -aG evsu <account>`, then sign out and in again.
Without that, `cd /srv/evsu-recruitment` fails with "Permission denied".

Most commands below run Django as the `evsu` account. They start with:

```bash
cd /srv/evsu-recruitment
sudo -u evsu .venv/bin/python manage.py <command>
```

You can also run a single server command directly from the laptop:

```powershell
ssh admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py check"
```

Commands that ask you to type something (a password, or the `nano` editor) need `ssh -t`.

---

## 3. Accounts

### First administrator account

```powershell
ssh -t admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py createsuperuser"
```

It asks for a username, an email address you can read, and a password (nothing appears while you
type it). Then give the account its staff roles (replace `<you>`):

```powershell
ssh admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py assign_role <you> 'System Administrator'"
ssh admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py assign_role <you> 'HR Personnel'"
ssh admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py assign_role <you> 'HR Head / Approver'"
ssh admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py seed_reference_data --sample-department"
```

Role names must be typed exactly: `Applicant`, `HR Personnel`, `HR Head / Approver`,
`Department Head`, `PSB Member`, `System Administrator`. An account cannot be both an applicant and
staff.

### Other accounts

- **Staff (groupmates):** sign in as the administrator, **System Admin → New staff account**. The
  person receives a "set your password" email.
- **Applicants:** register on the site (`/accounts/register/`). The verification email is sent to
  the address used.
- **Several test inboxes from one Gmail:** `name+test1@gmail.com`, `name+test2@gmail.com`, ... all
  arrive in the same inbox but count as different addresses.

### Forgotten password

Use **Forgot password?** on the sign-in page (email works on this copy). Or reset it on the server:

```powershell
ssh -t admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py changepassword <username>"
```

A staff member locked out of two-factor sign-in is reset by a System Administrator (**System Admin →**
the user → **Reset two-factor sign-in**).

---

## 4. Services

| Service | What it is | Restart after |
| --- | --- | --- |
| `evsu-dev` | The website (Django development server, port 8000) | Changing `.env` or pulling code |
| `evsu-ai-worker` | AI worker: reads verified documents | Changing `.env` or pulling code |
| `evsu-notifications.timer` | Retries failed notification emails every 5 minutes | Rarely |
| `mysql` | Database | Rarely |

```bash
sudo systemctl status evsu-dev evsu-ai-worker --no-pager     # running? (q to leave)
sudo systemctl restart evsu-dev evsu-ai-worker               # after changes
sudo systemctl stop evsu-ai-worker                           # pause the AI (jobs wait as Queued)
sudo systemctl start evsu-ai-worker
```

All of them start by themselves when the server boots. The unit files are in `deploy/systemd/`
(`evsu-dev.service` is for this development copy only).

---

## 5. Logs: where to look when something is wrong

```bash
journalctl -u evsu-dev -n 80 --no-pager          # website: errors, requests
journalctl -u evsu-ai-worker -n 50 --no-pager    # AI jobs: "attempt 1 succeeded; job Completed"
journalctl -u evsu-dev -f                        # follow live (Ctrl+C to stop)
```

In the browser, staff can also see AI job errors on the extraction page (**AI** column on the
application), and the **System Admin → Audit trail** records every action (search `ai.` for AI).

---

## 6. Email

This copy sends real email through Gmail (SMTP, port 587, with an app password). The settings are
the `EMAIL_*` and `DEFAULT_FROM_EMAIL` lines in `.env`.

```bash
sudo -u evsu .venv/bin/python manage.py sendtestemail you@example.com    # quick check
```

- Links in emails use `SITE_URL=http://192.168.1.107:8000`, so they open only on the home network.
- Ask testers to check the **spam** folder; Gmail-sent system mail is sometimes filtered.
- **To change the app password:** revoke the old one in the Google account (Security → App
  passwords), create a new one, then edit `.env` and restart:
  ```powershell
  ssh -t admin-mike@192.168.1.107 "sudo -u evsu nano /srv/evsu-recruitment/.env"
  ssh admin-mike@192.168.1.107 "sudo systemctl restart evsu-dev evsu-ai-worker"
  ```
  In `nano`: **Ctrl+O**, Enter to save, **Ctrl+X** to exit.
- **To stop sending real email** (print to the log instead): set
  `EMAIL_BACKEND=django.core.mail.backends.console.EmailBackend` and restart. Emails then appear in
  `journalctl -u evsu-dev`.

---

## 7. Updating to the latest code

After new work is pushed to GitHub:

```bash
cd /srv/evsu-recruitment
sudo -u evsu git pull --ff-only
sudo -u evsu .venv/bin/pip install -r requirements-linux.txt -r requirements-ai.txt   # if they changed
sudo -u evsu .venv/bin/python manage.py migrate
sudo -u evsu .venv/bin/python manage.py check
sudo systemctl restart evsu-dev evsu-ai-worker
```

If `git pull` refuses because of local changes, someone edited files on the server. See what changed
with `sudo -u evsu git status`. To throw those changes away and return to GitHub's version (the
`.env`, uploaded files, and the database are not affected):

```bash
sudo -u evsu git checkout -- . && sudo -u evsu git pull --ff-only
```

The untracked `.cache/` folder in the project is the service account's download cache; ignore it.

---

## 8. AI on this copy

AI extraction is on (`AI_EXTRACTION_ENABLED=True`). Verifying a document queues it; the worker reads
it within seconds; HR reviews the proposed items on the extraction page. Full test steps are in
`TESTING-AI.md` Part B.

```bash
sudo -u evsu .venv/bin/python manage.py make_ai_samples /srv/evsu-samples        # made-up documents
sudo -u evsu .venv/bin/python manage.py ai_extract_file /srv/evsu-samples/TOR_transcript.pdf --type TOR
sudo -u evsu .venv/bin/python deploy/ai_check.py                                 # runtime self-check
```

To copy the samples to your laptop for uploading:

```powershell
scp -r admin-mike@192.168.1.107:/srv/evsu-samples "$env:USERPROFILE\Downloads\"
```

---

## 9. Starting over with an empty database

Only when you really want to delete **all** accounts, applications, and uploaded files on this copy:

```bash
cd /srv/evsu-recruitment
sudo systemctl stop evsu-dev evsu-ai-worker
sudo mysql -e "DROP DATABASE evsu_recruitment_dev; CREATE DATABASE evsu_recruitment_dev CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
sudo -u evsu bash -c 'rm -rf private/documents/* private/exports/* private/reports/*'
sudo -u evsu .venv/bin/python manage.py migrate
sudo -u evsu .venv/bin/python manage.py seed_reference_data --sample-department
sudo systemctl start evsu-dev evsu-ai-worker
```

Then create the administrator account again (section 3). This cannot be undone.

---

## 10. Common problems

| Problem | What to do |
| --- | --- |
| The site does not open from the laptop | Same Wi-Fi/router? `ssh` works? Then `sudo systemctl status evsu-dev` and its log (section 5) |
| `ssh: connect ... timed out` | Server off, or its address changed (check the router's device list; reserve the address for `evsu-server`) |
| "Bad Request (400)" in the browser | Opened by a name or address not in `DJANGO_ALLOWED_HOSTS` in `.env`; use `192.168.1.107` |
| A page shows a database error after an update | `migrate` was not run (section 7) |
| AI badge stays **Queued** | Worker stopped: `sudo systemctl status evsu-ai-worker`, then its log |
| AI job **Failed** | Open the extraction page: the error is shown; **Retry** after fixing the cause |
| No email arrives | Spam folder; `sendtestemail` (section 6); the website log shows SMTP errors such as a revoked app password |
| Every two-factor code is refused | Server clock: `timedatectl` must show `synchronized: yes`; also the phone's automatic time |
| Server is slow | `free -h` and `top`; the AI worker yields to the website (`Nice=10`, `MemoryMax=3G`) |

---

## 11. Safety rules

- Keep port 8000 limited to the home network. This copy runs with debugging on and must never be
  exposed to the internet.
- Never commit or share `.env`, the Gmail app password, or database passwords.
- Use `sudo poweroff` / `sudo reboot` instead of the power button, so MySQL closes cleanly.
- Made-up data only (see the top of this guide).
- A temporary rule lets `admin-mike` use `sudo` without a password during remote setup. Remove it
  when setup is finished: `sudo rm /etc/sudoers.d/90-setup-temp`.
