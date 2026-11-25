# Release, upgrade check, and rollback (Stage 11)

This is the procedure for putting a new version of the system on a server that already has data,
and for going back if something is wrong. It applies to the production setup (`DEPLOY.md`) and,
with the service names changed, to the development copy (`DEV-SERVER.md`).

Commands run on the server from `/srv/evsu-recruitment` unless stated otherwise.

---

## 1. Before the release (any computer)

| # | Check | Command or action | Pass when |
| --- | --- | --- | --- |
| 1.1 | Automated tests on MySQL | `python manage.py test --noinput` | OK, no failures. 405 tests on 26 November 2026, about 27 minutes |
| 1.2 | Manual workflow with AI off and on | included in 1.1: `operations.test_acceptance` (two runs) and `ai_processing.test_acceptance` | Both pass |
| 1.3 | Fresh migrations | included in 1.1: the test database is built from the migrations each run | Passes |
| 1.4 | Upgrade of a populated database | Section 3 below, on the server | `UPGRADE CHECK PASSED` |
| 1.5 | Release notes | List the migrations and any new `.env` settings since the running version: `git log --oneline <old>..<new>`, `git diff <old> <new> --stat -- '*/migrations/*' .env.example` | Written down |
| 1.6 | Groupmate testing | `TESTING.md`, `TESTING-AI.md` | Results collected; failures fixed or accepted |
| 1.7 | HR acceptance (UAT) | HR walks through the manual workflow and the AI pages | HR signs off. **Not done yet** |

Write down the running version first: `sudo -u evsu git log --oneline -1`.

## 2. Release steps (on the server)

```bash
cd /srv/evsu-recruitment
OLD=$(sudo -u evsu git rev-parse --short HEAD); echo "running: $OLD"

# 2.1 Backup first, and prove it can be restored (restore check needs a System Administrator)
sudo -u evsu .venv/bin/python manage.py backup_create --requested-by <admin-username>
sudo -u evsu .venv/bin/python manage.py backup_verify
sudo -u evsu .venv/bin/python manage.py restore_check --requested-by <admin-username>

# 2.2 Short maintenance window: stop the AI worker so no job runs during migrate
sudo systemctl stop evsu-ai-worker

# 2.3 New code and dependencies (the same pinned versions that were tested)
sudo -u evsu git fetch -q && sudo -u evsu git checkout -q <new-commit>
sudo -u evsu .venv/bin/pip install -q -r requirements.txt
sudo -u evsu .venv/bin/pip install -q -r requirements-ai.txt      # AI server only

# 2.4 Database and static files
sudo -u evsu .venv/bin/python manage.py migrate
sudo -u evsu .venv/bin/python manage.py collectstatic --noinput    # production only

# 2.5 New .env settings from the release notes, then restart
sudo systemctl restart evsu-recruitment@public evsu-recruitment@staff evsu-ai-worker      # production
# development copy: sudo systemctl restart evsu-dev evsu-ai-worker
```

**After the release (smoke test, about 10 minutes):**

| # | Check | Pass when |
| --- | --- | --- |
| 2.6 | `sudo -u evsu .venv/bin/python manage.py check --deploy` (production) | "no issues" (one check, HSTS preload, is silenced on purpose) |
| 2.7 | `systemctl is-active evsu-recruitment@public evsu-recruitment@staff evsu-ai-worker` | all `active` |
| 2.8 | Open the applicant site and the staff site; sign in as staff (with two-factor) | Pages load |
| 2.9 | Open an existing application review and an approved roster; download its Excel file | Same as before the release |
| 2.10 | `journalctl -u evsu-ai-worker -n 20 --no-pager` | "AI worker ... started", no errors |

## 3. Upgrade check (before a release that has migrations)

This proves that the new version upgrades a database **filled by the old version**. It runs the
old version's own manual acceptance workflow against a scratch database (made-up data only):
registration, review, corrections, a released roster, a committee, and a hire. Then it upgrades
and compares. It never touches the real database or files.

```bash
OLD=<old-commit>; NEW=<new-commit>
W=/srv/evsu-upgrade; PY=/srv/evsu-recruitment/.venv/bin/python
DBUSER=$(sudo grep '^DB_USER=' /srv/evsu-recruitment/.env | cut -d= -f2)
sudo mysql -e "CREATE DATABASE evsu_upgrade_check CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  CREATE DATABASE evsu_upgrade_check_restore CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
  GRANT ALL PRIVILEGES ON evsu_upgrade_check.* TO '$DBUSER'@'localhost';
  GRANT ALL PRIVILEGES ON evsu_upgrade_check_restore.* TO '$DBUSER'@'localhost';"
sudo mkdir -p $W/private && sudo chown -R evsu:evsu $W
cd /srv/evsu-recruitment
sudo cp deploy/upgrade-check/populate.py deploy/upgrade-check/verify.py $W/   # from the NEW release
sudo -u evsu git worktree add -f $W/code $OLD
sudo sh -c "sed -e 's/^DB_NAME=.*/DB_NAME=evsu_upgrade_check/' -e '/^PRIVATE_STORAGE_ROOT=/d' \
  -e '/^BACKUP_ROOT=/d' .env > $W/code/.env
  echo PRIVATE_STORAGE_ROOT=$W/private >> $W/code/.env; echo BACKUP_ROOT=$W/backups >> $W/code/.env
  chown evsu:evsu $W/code/.env; chmod 600 $W/code/.env"
cd $W/code

# Old version: build the schema and fill it
sudo -u evsu $PY manage.py migrate -v 0
sudo -u evsu env UPGRADE_CHECK_DIR=$W PRIVATE_STORAGE_ROOT=$W/private \
  $PY manage.py shell -c "exec(open('$W/populate.py').read())"

# New version: upgrade and compare
sudo -u evsu git checkout -q $NEW
sudo -u evsu $PY manage.py migrate
sudo -u evsu env UPGRADE_CHECK_DIR=$W $PY manage.py shell -c "exec(open('$W/verify.py').read())"

# Backup and restore of the upgraded data (needs a System Administrator in the scratch database)
sudo -u evsu env DJANGO_SUPERUSER_PASSWORD="$(openssl rand -hex 16)" $PY manage.py createsuperuser \
  --noinput --username upgradeadmin --email upgradeadmin@example.com
sudo -u evsu $PY manage.py assign_role upgradeadmin "System Administrator"
sudo -u evsu $PY manage.py backup_create --requested-by upgradeadmin
sudo -u evsu $PY manage.py restore_check --requested-by upgradeadmin

# Clean up
cd /srv/evsu-recruitment
sudo -u evsu git worktree remove --force $W/code
sudo mysql -e "DROP DATABASE IF EXISTS evsu_upgrade_check; DROP DATABASE IF EXISTS evsu_upgrade_check_restore;"
sudo rm -rf $W
```

`verify.py` passes when:
- every old table keeps its row count;
- the new tables are empty;
- the optional new links (AI evidence, audit job, profile file) are empty on old records;
- the old evidence links still work;
- every roster export is byte-for-byte the same and the official ones still download;
- a qualification check runs on the upgraded application.

The restore check then confirms that the backup of the upgraded data restores with matching rows,
links (including the AI tables), and files.

On the old version, the populate step prints one expected note on the AI server: its last
assertion checks that no AI package is installed.

**Result on 26 November 2026:** passed. The old version was `ff7b3d6`, the last before Stage 10;
the new version was `29be989`. The upgrade applied 7 migrations to a database with two
applications, one hired, a released roster, 4 manual evidence links, and 89 audit entries. The
restore check also passed, with the AI link checks.

## 4. Rollback and recovery

MySQL cannot undo table changes inside a transaction. A failed or half-applied `migrate` is
therefore **not** rolled back by running `migrate` backwards. Choose the smallest step that fixes
the problem:

| Situation | Action | Data lost |
| --- | --- | --- |
| An AI feature misbehaves (wrong proposals, slow worker, model problem) | Switch it off in `.env` and restart: `AI_EXTRACTION_ENABLED=False`, `AI_COMPETENCY_MATCHING_ENABLED=False`, or `AI_QUALIFICATION_CHECKS_ENABLED=False`. Stop the worker if needed: `sudo systemctl stop evsu-ai-worker`. AI results are advisory, so the manual process continues unchanged | None |
| The new code has a bug, but `migrate` added only tables or optional columns (true for every Stage 10 migration) | Put the old code back without touching the database: `git checkout -q $OLD`, reinstall its requirements, restart. The old code ignores the new tables and nullable columns | None. New data in new tables stays for the next attempt |
| `migrate` failed halfway, or the data is damaged | Restore the backup from step 2.1 (below), then put the old code back | Everything after the backup. That is why step 2.2 starts the maintenance window right after the backup |
| A file or folder was lost | Restore only the files from the backup set's `files.zip` (`RECOVERY.md`, step 4) | None, if the backup is recent |

**Restoring the pre-release backup** follows `RECOVERY.md`, "Recovering the live system", with
the set created in step 2.1: stop the services, keep the current state as evidence, recreate the
database from `database.sql.gz`, unpack `files.zip` into a fresh `private/`, check out the **old**
commit (`$OLD`, the `application_commit` in the set's `manifest.json`), run `migrate` (no
changes are expected on the old commit) and `check --deploy`, start the services, and verify. The
MySQL root password is entered by the person doing the recovery. Record every rollback in the
development log: what happened, what was done, and what was lost.

**After a rollback:** fix the problem, run section 1 again (including the upgrade check) on the
fixed version, and release again.

## 5. Not covered yet

- **Load test.** It needs the production setup (Gunicorn and Nginx, not the development server)
  and the institution's approval for the target environment. Planned: about 50 simultaneous
  applicants browsing and uploading, while HR reviews and the AI worker reads documents.
- **HR acceptance (UAT).** Planned with HR; see 1.7.
- **The production deployment itself.** It is waiting on the internet access option and the
  domain (see `AI-SERVER.md` step 9 and the deferred Cloudflare / Tailscale choice).
