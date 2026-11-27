# Staff help guide

The staff assistant answers from these articles only. Each article starts with
"## Title" and a "Roles:" line (HR, System Administrator, or both). An optional
"Also asked as:" line after it lists other ways of asking, separated by "|"; the
assistant matches questions against these too. Keep each article short and
factual, and use the exact page and button names. HR should review this guide;
changing it changes what the assistant can say.

## Verifying uploaded documents
Roles: HR
Open **HR workspace → Document verification queue**. It lists the latest file versions waiting for verification, for vacancies you are assigned to, oldest first. Press **Verify** on a row, open the file, and choose a status: **Verified**, **Incomplete**, **Invalid**, or **Rejected**. Any status other than Verified needs a note explaining why. After that, the application review page shows a greyed-out **✓ Verified** (or **✓ Done** for the other statuses) instead of Verify. If the outcome was a mistake, press **Change** under it, choose the correct status, and explain why in the notes; the earlier outcome stays in the history and the HR Head / Approver is notified. Verifying never changes the application's status by itself. When AI extraction is on, verifying a document also queues it for AI reading.

## Starting the review of an application
Roles: HR
Open the vacancy, then the application, and press **Start review**. The status changes from Submitted to Under review and the applicant sees it. The review page shows the submitted information, the documents with their verification status, the facts to review, and the qualification standards of the published requirement version.

## Reviewing application facts
Roles: HR
On the application review page, each fact (education, experience, training, eligibility, license) starts as Pending review. Press **Accept** if the evidence supports it, or **Reject** with a reason. Once a fact is accepted or rejected, its buttons are greyed out and show the decision. If the decision was a mistake, press **Change decision**, write why, and press **Change to Rejected** or **Change to Accepted**; the earlier decision stays in the audit trail and the HR Head / Approver is notified. For experience and training, also mark **Relevant** or **Not relevant** to the vacancy; after that both are greyed out, and a mistake is fixed with **Change relevance** and a reason. To fix a wrong value, use **Correct**: a reason is required, the old value is kept in the history, and a new accepted fact replaces it. **Add fact manually** records a qualification that only appears in a document, with the reason and the document as evidence.

## Requesting corrections from an applicant
Roles: HR
On a submitted or under-review application, press **Request correction**. Write what must be fixed, choose the document types to replace if any, and set a deadline. The application becomes Correction requested and the applicant is notified. The applicant uploads replacements and resubmits before the deadline; the original files stay in the history. If the applicant needs more time, use **Grant late exception / extend** with a reason.

## Recording a qualification outcome
Roles: HR
Under **Qualification review** on the application, each published standard has **Record outcome**. Choose **Meets Requirement**, **Does Not Meet Requirement**, **Needs Review**, or **Not Required**, tick the supporting accepted facts, and write a reason (required for Does Not Meet and Needs Review). Outcomes are HR decisions; they never change shortlisting or roster inclusion by themselves.

## Using the advisory qualification checks
Roles: HR
Under **Qualification review**, press **Run qualification checks**. Fixed rules compare the facts you accepted with each standard and suggest an outcome with step-by-step reasoning: only accepted facts count, experience and training count only when marked Relevant, overlapping jobs count once, and ongoing degrees, other fields, expired license cards, and written requirements go to Needs Review. The page stays on the application and opens each standard's reasoning and the facts used inside the card; use **Hide reasoning** / **Show reasoning** to fold each one. **Record outcome starting from this check** fills in the outcome form for you to edit and submit. When facts change, the check shows **Out of date**; run it again.

## Reviewing AI extraction results
Roles: HR
When AI extraction is on, verified documents are read in the background. In the application's Documents section, the **AI** column shows the status; open the results to see the document text beside the proposed items. For each item choose **Accept**, edit and accept (a reason is required; the original proposal is kept), **Link** it to an existing fact so the same qualification is not counted twice, or **Reject** with a reason. Accepted items become ordinary accepted facts with the document linked as evidence. **HR workspace → AI extraction results** lists every result for your vacancies: waiting for review, queued, failed, and reviewed, plus a summary of how items were handled per document type. A failed job can be retried from its page.

## Using competency matching
Roles: HR
On the application, the **Competency matching** card has **Run competency matching**. The AI worker compares each vacancy competency with the accepted qualification wording and shows a similarity number from -1 to 1 for each pair, with both texts. It is not a score, percentage, or ranking, and it never changes the application or roster. It needs accepted facts or a verified document the AI has read. Names, contact details, schools, and employers are never used in the facts compared. Under each competency, **From verified documents (not reviewed by HR)** also lists up to three passages from the verified documents that are close in meaning, with the document and page: use them to find qualifications to review and accept. They do not change the similarity of the facts. When facts or verified documents change, it shows **Out of date**; run it again.

## Creating and publishing a vacancy
Roles: HR
Open **HR workspace → Vacancies and requirements** and press **New vacancy**. Choose a position from the position catalog: its title, description, document checklist, qualification standards, and competencies are copied into the draft. Set the department, number of positions, opening date, and deadline. Adjust requirements with **+ Document**, **+ Qualification standard**, and **+ Subject / competency**, then press **Publish vacancy**. When adding a qualification standard, the form shows only the fields the chosen criterion needs. For a qualification standard of type **Eligibility** or **Professional license**, a dropdown lists the names from the licenses and eligibilities list; choose one, or choose **Other (not in the list)** and type the name. Using a listed name lets the advisory qualification check recognise other spellings applicants use (for example "LET passer" for **Professional Teacher**). Publishing freezes that requirement version; later changes go into a new draft version that applicants see only after **Publish version**. A committee is created automatically when the vacancy is first published. **Close applications** ends the application period early.

## Managing the position catalog
Roles: HR
Open **HR workspace → Position catalog**. **New position** creates a reusable position with a default description, document checklist, qualification standards, and competencies. New vacancies copy these defaults; editing a position never changes existing vacancies. A used position cannot be deleted: use **Deactivate** so it can no longer start vacancies, and **Reactivate** to use it again. On a never-published draft, **Load defaults from a position** replaces the draft's title and requirements after confirmation.

## Preparing the official roster
Roles: HR
Open the vacancy and choose **Official roster**, then **Generate draft**. The roster includes every submitted application for the vacancy, including withdrawn and not selected ones; drafts are never included. Rows with missing or unverified information are flagged: verify the information or acknowledge each flagged row. Fill in the **Signatories**. Download **Draft Excel** or **Draft PDF** to check the layout, then press **Send for approval**. If application information changes after generation, the roster asks you to **Regenerate** first. An approved roster that becomes outdated stays unchanged; **Prepare new version** starts a replacement that goes through approval again.

## Roster approval and release
Roles: HR
A permitted approver opens **Rosters awaiting approval** and chooses **Approve** or **Return for correction** with comments. After approval, **Release official roster** creates the official Excel and PDF files, which never change afterwards. HR prepares rosters; approval is done by the HR Head / Approver.

## Committees, interviews, and evaluations
Roles: HR
Open the vacancy and choose **Committees & interviews**. Add members to the committee with **Add member** and a role such as chair. Members must declare conflicts; a conflicted member cannot assess the affected applicant. Schedule an interview from the applicant's **Selection & decisions** page with **Schedule interview**, choosing the panel, time, mode, and location. Rescheduling or cancelling keeps the old schedule in the history. After the interview, record attendance; panel members write and submit their evaluations, and changes to a submitted evaluation need an amendment reason.

## Recording and releasing decisions
Roles: HR
On the application's **Selection & decisions** page, use **Record shortlisting**, **Record committee recommendation**, **Record final outcome**, or **Record appointment**, each with a reason. A recommendation is not a hire: Hired requires a recorded appointment with its reference and date. Recorded decisions stay internal until released: **HR workspace → Shortlisting decisions awaiting release** lists them, and **Release** shows the outcome to the applicant and notifies them. Committee recommendations stay internal.

## Searching applications and reports
Roles: HR
**HR workspace → Search applications across your vacancies** finds applications by name, reference, status, and other filters, limited to vacancies you are assigned to. **Recruitment analytics and reports** shows counts such as submitted applications, shortlisted, interviews, and hired, per vacancy or period, and can be downloaded.

## Creating staff accounts and assigning roles
Roles: System Administrator
Open **System Admin → User accounts and roles** and press **New staff account**. Enter the name, email, roles, and a reason. The person receives a single-use link to choose their own password on the staff site. To change roles or deactivate an account, press **Manage** on the user, change the roles or the active setting, and **Save changes**. Applicant accounts and staff accounts are separate; a staff role cannot be added to an applicant account. **Send set-password link** emails a new link if the person lost theirs.

## Staff two-factor sign-in
Roles: HR, System Administrator
Staff sign in at the staff sign-in page with their password and a six-digit code from an authenticator app. The first time, **Set up two-factor sign-in** shows a QR code to scan and gives recovery codes; keep them somewhere safe, because each recovery code works once instead of the app code. If a staff member loses their phone and recovery codes, a System Administrator opens the user under **User accounts and roles** and uses **Reset two-factor sign-in**. Another System Administrator must reset your own.

## Passwords and locked accounts
Roles: HR, System Administrator
If you forget your password, use **Forgot password** on the sign-in page; a reset link is emailed. After too many wrong passwords, sign-in is blocked for a while and then works again. A System Administrator can send a new set-password link from **User accounts and roles**. Change your password under **My account**.

## Backups and restore checks
Roles: System Administrator
**Backup and restore monitoring** (account menu, **Backups and restore checks**) shows recent backup sets and restore checks. A backup set holds the database, uploaded documents, roster files, reports, and templates together. Backups run daily on the server with `manage.py backup_create`; `manage.py backup_verify` confirms stored sets are intact; `manage.py restore_check --requested-by <username>` restores a set into a separate database and checks rows, links, and files, without touching the live system. Run a restore check regularly. The full recovery procedure is in deploy/RECOVERY.md and the release procedure in deploy/RELEASE.md.

## Audit trail
Roles: System Administrator
The **Audit trail** (account menu) lists recorded actions: who did what, when, from which address, and why. Filter by action (for example `ai.` for AI events, `fact.` for fact reviews), user, or date, and export the results. Audit entries cannot be edited or deleted. The HR Head / Approver can also open it.

## Licenses and eligibilities list
Roles: System Administrator
Open the account menu → **Licenses and eligibilities**. It lists the professional licenses (PRC) and civil service eligibilities (CSC) offered in the dropdowns: to HR in qualification standards, to applicants in their profile, and to HR when adding or correcting a fact. **Other (not in the list)** lets anyone type a name that is missing. Each name can have **other spellings**, one per line (for example "LET" and "LET passer" for **Professional Teacher**); the advisory qualification check treats them as the same credential. Use **Add a name** for a missing one and **Edit** to change spellings. To stop suggesting a name, uncheck **Suggest this name** instead of deleting it. When renaming, keep the old name as another spelling so existing entries still match. The starting list was drafted from the PRC and CSC lists and should be checked against the official ones.

## Official roster templates
Roles: System Administrator
The official roster Excel template is registered on the server with `manage.py register_roster_template`, then approved under **Official roster templates**, where the signatories are set. Rosters can only be generated when an approved template exists for the recruitment type; otherwise HR sees "No approved roster template exists". The real template file is kept off the repository because it holds staff names and logos.

## Email deliveries
Roles: System Administrator
**Email deliveries** lists notification emails with their status. Failed deliveries are retried automatically by the notifications timer; a delivery can also be retried by hand. If many fail, check the email settings in the server's .env file and send a test with `manage.py sendtestemail <address>`.

## AI settings and the AI worker
Roles: System Administrator
Each AI feature has its own switch in the server's .env file: `AI_EXTRACTION_ENABLED` (document reading), `AI_QUALIFICATION_CHECKS_ENABLED` (rule-based checks), and `AI_COMPETENCY_MATCHING_ENABLED` (SBERT matching). Restart the services after changing them. Turning a feature off is safe: AI results are advisory and the manual process works the same. Extraction and matching run in the `evsu-ai-worker` service; check it with `systemctl status evsu-ai-worker` and its log with `journalctl -u evsu-ai-worker -n 50`. Failed jobs are retried automatically a few times and can then be retried by HR. The staff assistant uses the separate `ollama` service and can be switched off with `ASSISTANT_ENABLED=False`.

## What AI never does
Roles: HR, System Administrator
AI in this system only proposes, checks, and compares. It never changes an application's status, never shortlists, rejects, or hires anyone, never records or releases decisions, never changes or reorders the official roster, and never processes a document HR has not verified. HR records every official fact, outcome, and decision. This assistant also never reads applicant or application data: it answers only from this help guide.

## What the help assistant can do for you
Roles: HR, System Administrator
Besides answering how-to questions, the assistant can do a few things for you after you press **Confirm** on the card it shows. It can open a page ("open the verification queue", "open the roster of VAC-1A2B3C4D", "open the review of APP-1A2B3C4D"). HR can also ask it to show pending work ("show my pending work"), list applications ("list applications under review for the Math vacancy"), run the advisory qualification checks or competency matching for an application, and retry failed AI reading jobs. It finds records only among those you are allowed to see; if several match, it shows one card for each so you can choose. It never makes decisions, shortlists, hires, approves or releases rosters, verifies documents, accepts or rejects facts, changes roles, or deletes anything: those are done on their own pages. Nothing happens until you press Confirm, and confirmed actions are recorded in the audit trail under your name.

## Reviewing questions the help assistant could not answer
Roles: HR, System Administrator
When saving is switched on, questions the assistant could not answer, and answers someone marked **This did not help**, are saved without the asker's name. Names, references, email addresses, and long numbers are masked, and saved questions are deleted after 90 days. HR opens them from **HR workspace → Help assistant: questions it could not answer**; System Administrators from the account menu → **Help assistant questions**. Identical questions are grouped under the closest article. For each one choose **Link to article** if an existing article answers it with other words (edit the phrase if needed; the assistant matches that wording from then on), **New article added** once the help guide has a new article for it, or **Out of scope** if the assistant should not answer it. **Delete** removes a question that still shows personal data. Phrases you added are listed under "Other ways of asking", where **Remove** takes one away.
