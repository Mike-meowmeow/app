# Testing guide: AI document extraction (Stage 10, first round)

This guide tests the new AI features:

- verified documents are read automatically, with text from digital PDFs or OCR for scans and images;
- the AI proposes education, experience, training, eligibility, and license items;
- HR reviews each item: accept, edit and accept, link to an existing fact, or reject;
- jobs are cancelled when a document is replaced or its verification is withdrawn;
- failures are retried, and manual review always keeps working.

**Everything the AI produces is a suggestion.** It must never change an application's status,
shortlist, decisions, or the official roster. If you ever see that happen, report it as a failure.

There are two parts:

| Part | Where | Who can do it | Time |
| --- | --- | --- | --- |
| **A** | Your own computer | Everyone | About 30 minutes |
| **B** | The AI server's development copy, `http://192.168.1.107:8000` | Someone on the same home network as the server, with help from the person who manages it | About 1 hour |

Part A uses no AI packages: it checks the automated tests and what happens when the AI runtime is
missing. Part B uses the real OCR and models on the server.

**Use only the made-up sample documents** described below. Never upload real applicant documents
or your own records.

Commands are for Windows PowerShell, run from the `app` folder.

---

## Part A. On your own computer

### A1. Get the latest code

```powershell
cd "C:\path\to\app"
git pull
.\.venv\Scripts\python.exe manage.py migrate
```

**Expected:** `git log --oneline -1` shows the Stage 10 commit, the folder `ai_processing` exists,
and `migrate` applies `ai_processing.0001_initial`, `audit.0002_auditlog_job`, and
`documents.0004_factevidence_extraction_item` (or says there is nothing to apply).

### A2. Automated tests (about 20 minutes)

```powershell
.\.venv\Scripts\python.exe manage.py test --noinput
```

**Expected:** a final line `OK`, after about 356 tests. To run only the AI tests (about 3 minutes):

```powershell
.\.venv\Scripts\python.exe manage.py test ai_processing --noinput
```

**Expected:** `Ran 33 tests` and `OK`.

### A3. Make the sample documents

```powershell
.\.venv\Scripts\python.exe manage.py make_ai_samples "$env:USERPROFILE\Downloads\evsu-ai-samples"
```

**Expected:** `Wrote 7 made-up sample files`. Open one: every file says
**SAMPLE - MADE-UP DATA - NOT A REAL RECORD** at the top. Keep this folder for Part B.

### A4. What happens without the AI runtime

Your computer does not have the OCR and AI packages, so this checks that a failure is reported
clearly and that HR can carry on manually.

1. Add these lines to your `.env` (the last one removes the wait between retries):
   ```ini
   AI_EXTRACTION_ENABLED=True
   AI_RETRY_SECONDS=0
   ```
2. Start the system as in TESTING.md Part 3 (or one plain `runserver`), and use an application
   from TESTING.md Part 7 that has a submitted PDF.

| # | Step | Expected |
| --- | --- | --- |
| A4.1 | As HR, open the application and click **Verify** on the PDF; choose **Verified** | Back on the application, the Documents table has an **AI** column showing **Queued** |
| A4.2 | In a new terminal: `.\.venv\Scripts\python.exe manage.py ai_worker --once` | A line ending `attempt 1 failed; job Queued. The AI runtime is not installed here...` |
| A4.3 | Run the same command two more times | The third time: `job Failed` |
| A4.4 | Refresh the application; click the **Failed** badge | The extraction page shows **Failed**, the error "The AI runtime is not installed here (...)", and 3 attempts |
| A4.5 | Go back and **Accept** a fact in **Application facts**, or **Add fact manually** | Works as before: manual review does not depend on the AI |
| A4.6 | On the extraction page click **Retry**, then run `ai_worker --once` again | "Extraction queued again."; the run fails the same way, a fourth attempt is listed, and the job is **Queued** for its next try (a retry gives it a fresh set of 3 runs) |

3. **Remove the two lines from your `.env` afterwards** (or set `AI_EXTRACTION_ENABLED=False`), then
   restart the server. **Expected:** the AI column disappears for applications without AI jobs.

---

## Part B. On the AI server (real OCR and models)

The server's development copy is reachable only from the same home network as the server. Emails
(verification and set-password links) are not sent; they are written to the server's log. The
person who manages the server can show them with:

```powershell
ssh admin-mike@192.168.1.107 "journalctl -u evsu-dev -n 80 --no-pager"
```

### B1. Accounts (the person who manages the server)

Create your own administrator account. It asks for a username, email, and password of your choice:

```powershell
ssh -t admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py createsuperuser"
```

Give it the staff roles (replace `<you>` with the username you chose):

```powershell
ssh admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py assign_role <you> 'System Administrator'"
ssh admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py assign_role <you> 'HR Personnel'"
ssh admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py assign_role <you> 'HR Head / Approver'"
ssh admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py seed_reference_data --sample-department"
```

Open `http://192.168.1.107:8000/accounts/staff/login/` and sign in. **Expected:** the HR workspace.

### B2. A vacancy that accepts the sample documents

| # | Step | Expected |
| --- | --- | --- |
| B2.1 | **HR → Vacancies → New vacancy** (or from a catalog position), sample department, opening now, deadline next week | Draft created |
| B2.2 | Add document requirements: **Personal Data Sheet** (required, 1 file), **Transcript of Records** (2 files), **Diploma** (1), **PRC license / eligibility certificate** (1), **Training certificates** (2), **Certificate of employment / service record** (1) | All six listed |
| B2.3 | Add a qualification standard (Education, Bachelor's) and a competency, then **Publish vacancy** | Published |

### B3. The test applicant

| # | Step | Expected |
| --- | --- | --- |
| B3.1 | In a private window, open `http://192.168.1.107:8000/accounts/register/`; username `aitest`, email `aitest@example.com` | "Check your email"; the verification link is in the server log (see above) |
| B3.2 | Open the link, sign in, complete **My profile** | Saved |
| B3.3 | In **My profile**, add one education record exactly as: Bachelor's degree, program `Bachelor of Secondary Education Major in Mathematics`, institution `Leyte Sample State University`, completed, date completed `2015-03-25` | Saved (used in B6.3 to test linking) |
| B3.4 | Apply to the vacancy and upload the files from `Downloads\evsu-ai-samples` as in the table below; for the Personal Data Sheet use any PDF; then **Submit** | Application submitted |

| Upload as | File |
| --- | --- |
| Personal Data Sheet | any PDF (for example `DIPLOMA_masters.pdf` again) |
| Transcript of Records | `TOR_transcript.pdf` and `TOR_scanned.pdf` |
| Diploma | `DIPLOMA_masters.pdf` |
| PRC license / eligibility certificate | `PRC_license.pdf` |
| Training certificates | `TRAINING_certificate.pdf` and `TRAINING_scanned.png` |
| Certificate of employment | `EMPLOYMENT_certificate.pdf` |

### B4. Verification starts the AI

| # | Step | Expected |
| --- | --- | --- |
| B4.1 | As HR, open the application. Before verifying anything, look at the **AI** column | Empty for every document: unverified documents are never read |
| B4.2 | **Start review**, then **Verify** each document as **Verified** | After each, the AI column shows **Queued** |
| B4.3 | Wait about 10 seconds and refresh | Each badge becomes **Completed**, except the Personal Data Sheet, which shows **Needs Review** |

### B5. What the AI read

Click each badge. The extraction page shows the proposed items on the left and the extracted text
on the right, with the source lines highlighted. Compare with this table:

| File | Read as | Expected items |
| --- | --- | --- |
| `TOR_transcript.pdf` | digital text | **Education**: Bachelor's degree, Bachelor of Secondary Education Major in Mathematics, Leyte Sample State University, Completed, 2015-03-25 |
| `TOR_scanned.pdf` | **OCR** (confidence about 0.95) | **Education**: Bachelor of Science in Information Technology, Sample State College, Completed, 2012-04-02 |
| `DIPLOMA_masters.pdf` | digital text | **Education**: Master's degree, Master of Arts in Education, Sample Institute of Technology, Completed, 2019-04-30 |
| `PRC_license.pdf` | digital text | **License**: Professional Teacher, 1234567, Professional Regulation Commission, issued 2015-08-10, expires 2027-09-01; **Eligibility**: Licensure Examination for Professional Teachers, rating 85.40, 2015-03-29 |
| `TRAINING_certificate.pdf` | digital text | **Training**: Outcomes-Based Teaching and Learning Workshop, Sample Teachers Training Center, 24 hours, 2019-03-03 to 2019-03-05 |
| `TRAINING_scanned.png` | **OCR** | **Training**: Classroom Assessment Seminar, Sample Division Office, 8 hours, 2021-10-12 |
| `EMPLOYMENT_certificate.pdf` | digital text | **Experience**: Part-time Instructor, Sample College of Ormoc, 2016-06-01 to 2020-05-31 |
| Personal Data Sheet | digital text | No items; the warning "No item rules exist yet for this document type (PDS)" |

| # | Step | Expected |
| --- | --- | --- |
| B5.1 | Check every row of the table above | Values match. Write down any value that differs (that is useful evaluation data, not necessarily a failure) |
| B5.2 | Every extraction page | Shows the **Advisory** notice, the method and version, page numbers and line numbers for each item |
| B5.3 | Open the page as a user without review rights (the applicant `aitest`), by pasting the address | "403 Forbidden" |

### B6. HR review of the items

| # | Step | Expected |
| --- | --- | --- |
| B6.1 | `DIPLOMA_masters.pdf`: **Accept** | "Item accepted as an application fact." The item shows **Accepted** with your name and time |
| B6.2 | Back on the application, **Application facts** | A new Education fact, **Accepted**, with the badge **From AI extraction, HR reviewed**, and evidence "Diploma v1 (page 1, AI extraction)" |
| B6.3 | `TOR_transcript.pdf`: the item says **Same as an existing education fact: link it instead of adding it again**. Under **Link as evidence**, choose the Bachelor of Secondary Education fact (not the Master's from B6.1) | "Item linked as evidence"; no second Education fact appears; the profile fact now lists the transcript as evidence |
| B6.4 | `TOR_scanned.pdf`: **Edit and accept**, change the institution to `Sample State College of Leyte`, leave the reason empty, submit | The form asks for a reason |
| B6.5 | Enter a reason and submit | The item shows **Accepted with corrections**; the proposal on the left still shows the original value; the new fact has your value |
| B6.6 | `TRAINING_scanned.png`: **Reject** without a reason | "A reason is required to reject an item." |
| B6.7 | Reject with a reason | **Rejected**; no fact is created |
| B6.8 | Try **Accept** again on any reviewed item (refresh an old tab and press it) | "This item was already reviewed." |
| B6.9 | Accept the rest (PRC license, eligibility, training certificate, employment) | Facts created; experience and training facts show **Not assessed** relevance, which HR sets as before |
| B6.10 | Check the application's status and **Selection & decisions** | Unchanged by any AI step: still **Under review**, nothing shortlisted |
| B6.11 | **System Admin → Audit trail**, enter `ai.` in **Action starts with**, search | Entries such as `ai.job_queued`, `ai.extraction_completed`, `ai.item_accepted`, `ai.item_corrected`, `ai.item_linked`, `ai.item_rejected`, with your name where you acted |

### B7. Replacing or withdrawing a document

| # | Step | Expected |
| --- | --- | --- |
| B7.1 | As HR, **Request correction** and tick **Training certificates** | Correction requested |
| B7.2 | As `aitest`, replace `TRAINING_certificate.pdf` with `TRAINING_scanned.png` and resubmit corrections | Submitted |
| B7.3 | As HR, open the application | The old version's AI badge shows **Invalidated**; its extraction page says why ("Replaced by version 2") and has no review buttons |
| B7.4 | Verify the new version | A new job: **Queued**, then **Completed** |
| B7.5 | Open `EMPLOYMENT_certificate.pdf` and **Verify** it again, this time as **Invalid** with a reason | Its AI badge shows **Invalidated** ("Verification changed to Invalid") |
| B7.6 | Verify it again as **Verified** | A new job is queued and completes; the earlier one stays invalidated |

### B8. When the worker is stopped (the person who manages the server)

| # | Step | Expected |
| --- | --- | --- |
| B8.1 | `ssh admin-mike@192.168.1.107 "sudo systemctl stop evsu-ai-worker"` | No output |
| B8.2 | Repeat B7.5 and B7.6 on another document (Invalid, then Verified) | The new job stays **Queued**; the site works normally |
| B8.3 | `ssh admin-mike@192.168.1.107 "sudo systemctl start evsu-ai-worker"` and wait 10 seconds | The job completes by itself: nothing was lost while the worker was down |

### B9. One file from the command line (optional)

```powershell
ssh admin-mike@192.168.1.107 "cd /srv/evsu-recruitment && sudo -u evsu .venv/bin/python manage.py make_ai_samples /tmp/s && sudo -u evsu .venv/bin/python manage.py ai_extract_file /tmp/s/PRC_license.pdf --type PRC"
```

**Expected:** JSON with the pages, any warnings, and the same two items as in B5. Nothing is saved.

---

## Results checklist

Copy this table into your report and fill in **Pass** or **Fail** for each row.

| Part | Result | Notes |
| --- | --- | --- |
| A1 Latest code and migrations | | |
| A2 Automated tests (number of tests: ___) | | |
| A3 Sample documents | | |
| A4 Without the AI runtime (A4.1-A4.6) | | |
| B1 Accounts on the server | | |
| B2 Vacancy | | |
| B3 Test applicant and application | | |
| B4 Verification starts the AI (B4.1-B4.3) | | |
| B5 What the AI read (B5.1-B5.3; list any differing values) | | |
| B6 HR review (B6.1-B6.11) | | |
| B7 Replacing or withdrawing a document (B7.1-B7.6) | | |
| B8 Worker stopped (B8.1-B8.3) | | |
| B9 Command line (optional) | | |

**For each failure, include:**
- the step number;
- what you did;
- what you expected and what happened instead;
- a screenshot;
- for the server, the last lines of `journalctl -u evsu-ai-worker -n 50 --no-pager` (ask the person
  who manages the server).

Never include passwords, `.env` files, or real documents.
