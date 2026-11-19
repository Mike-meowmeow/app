# Backup and recovery procedure

A backup set contains everything needed to recover the system at one point in time:

| File | Contents |
| --- | --- |
| `database.sql.gz` | `mysqldump --single-transaction` of the application database |
| `files.zip` | `documents/` (uploaded files), `exports/` (roster Excel/PDF), `reports/`, `templates/` |
| `manifest.json` | SHA-256 of both files, file counts, row counts, applied migrations, code commit |

The database is dumped first and the files are copied after it. Stored files are written once and
never changed, and each file exists before the database row that refers to it, so every file the
dump refers to is in the archive.

Backup frequency, retention, off-site copies, encryption, and recovery targets are operational
requirements that HR and IT still need to set. The defaults below are a starting point.

## Daily backup

The `evsu-backup.timer` unit runs every night at 01:30:

```bash
.venv/bin/python manage.py backup_create      # creates the set and records it
.venv/bin/python manage.py backup_verify      # re-checks every stored set's checksums
```

On Windows development machines, run the same commands with `.\.venv\Scripts\python.exe`, or use
Task Scheduler.

A failed backup is recorded, and every System Administrator gets an in-app notification (with an
email copy). The **Backups and restore checks** page warns when the last successful backup is older
than `BACKUP_MAX_AGE_HOURS` (default 26).

**Off-site copies:** copy each completed set folder from `BACKUP_ROOT` to separate storage, such as
another server or an institutional backup service, using `rsync` or the institution's tool. Keep
these copies encrypted and access-restricted, because they contain personal data. Example retention:
14 daily sets plus one per month for a year. This needs HR/IT confirmation.

## Monthly restore check

Run this after setup, after every upgrade, and at least once a month:

```bash
.venv/bin/python manage.py restore_check --requested-by <system-admin-username>
.venv/bin/python manage.py restore_check 20261117-210706-6c8af9 --requested-by <admin> --keep
```

What the check does:
- Loads the set into the isolated database `<DB_NAME>_restore` and extracts the files to a scratch
  folder under `BACKUP_ROOT/_restore_check/`. It never touches the live database or `private/`.
- Verifies:
  - the applied migrations;
  - row counts (when the data didn't change during the backup);
  - ownership links (applications, applicants, users, vacancies, documents, roster entries, exports,
    decisions);
  - that every file referenced by document versions, roster exports (including approved official
    files), reports and templates exists with its recorded SHA-256.
- Records the result as a restore run, shown on **Backups and restore checks**.
- Drops the restore database and scratch files afterwards, unless `--keep` is given.

The application account needs access to `<DB_NAME>_restore`:
- Development: run `py setup_db.py` once.
- Server: see the GRANT in DEPLOY.md step 3.

## Recovering the live system

Recover only from a set whose restore check passed.

1. Announce downtime and stop the application:
   `sudo systemctl stop evsu-recruitment@public evsu-recruitment@staff evsu-notifications.timer evsu-backup.timer`.
2. Keep the current state as evidence. Run `backup_create` if the database still works, and copy
   `private/` aside.
3. Recreate the database:
   ```bash
   mysql -u root -p -e "DROP DATABASE evsu_recruitment; CREATE DATABASE evsu_recruitment \
       CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;"
   gunzip -c /srv/evsu-recruitment-backups/<set>/database.sql.gz | mysql -u root -p evsu_recruitment
   ```
4. Restore the files:
   ```bash
   sudo -u evsu mv /srv/evsu-recruitment/private /srv/evsu-recruitment/private.before-restore
   sudo -u evsu mkdir /srv/evsu-recruitment/private
   sudo -u evsu unzip /srv/evsu-recruitment-backups/<set>/files.zip -d /srv/evsu-recruitment/private
   sudo chmod -R 750 /srv/evsu-recruitment/private
   ```
5. Check out the code commit recorded in `manifest.json` (`application_commit`), or a later one, then:
   - run `migrate` with the migration account;
   - run `check --deploy` for each site (DEPLOY.md, section 5);
   - start both services.
6. Verify:
   - sign in as an administrator;
   - open a recent application and download a document;
   - download an approved roster file;
   - check the audit trail;
   - run `restore_check` on a new backup.
7. Record what happened, the set used and the data-loss window in the audit trail or incident log.

Anything recorded after the backup time is not in the restored system. Tell affected users to
resubmit or re-enter it.
