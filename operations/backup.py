"""Consistent backup sets and isolated restore verification (FR-020).

A backup set is one folder under BACKUP_ROOT:

    <set id>/database.sql.gz   mysqldump --single-transaction (one point in time)
    <set id>/files.zip         documents/, exports/, reports/, templates/
    <set id>/manifest.json     checksums, row counts, migrations, file counts

Consistency: the database is dumped first, then the files are copied. Stored
files are written once and never changed (document versions, roster exports,
reports, templates), and a file is always written before the database row
that references it commits, so every file referenced by the dump already
exists when the files are copied. Files created after the dump are harmless
extras.

A restore check loads a set into the isolated database <DB_NAME>_restore and
a scratch folder (never the live database or private/ folder), then verifies
migrations, row counts, ownership links, and that every referenced file -
including approved roster exports - is present with its recorded SHA-256.
Real recovery onto the live server is the documented manual procedure in
deploy/RECOVERY.md, after a successful check.
"""

import gzip
import hashlib
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.db import connection
from django.utils import timezone

from accounts import roles
from audit import services as audit

from .models import BackupRun, RestoreRun, RunStatus

MANIFEST_VERSION = 1
CHUNK = 1024 * 1024
PROJECT_APPS = ('core', 'accounts', 'audit', 'documents', 'recruitment', 'applicants', 'matrix',
                'committee', 'notifications', 'analytics', 'operations')
# (table, area) pairs whose storage_key/sha256 columns point at private files.
FILE_TABLES = (('document_versions', 'documents'), ('roster_exports', 'exports'),
               ('analytics_reports', 'reports'), ('roster_templates', 'templates'))
# Ownership links that must survive a restore: (description, child, column, parent).
LINKS = (
    ('applications -> applicants', 'applications', 'applicant_id', 'applicants'),
    ('applicants -> users', 'applicants', 'user_id', 'users'),
    ('applications -> vacancies', 'applications', 'vacancy_id', 'vacancies'),
    ('documents -> applications', 'documents', 'application_id', 'applications'),
    ('document versions -> documents', 'document_versions', 'document_id', 'documents'),
    ('roster entries -> rosters', 'applicant_roster_entries', 'roster_id', 'applicant_rosters'),
    ('roster entries -> applications', 'applicant_roster_entries', 'application_id',
     'applications'),
    ('roster exports -> rosters', 'roster_exports', 'roster_id', 'applicant_rosters'),
    ('decisions -> applications', 'recruitment_decisions', 'application_id', 'applications'),
)


class BackupFailed(Exception):
    pass


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def backup_root():
    return Path(settings.BACKUP_ROOT)


def file_areas():
    from matrix.services import template_root
    return {'documents': Path(settings.DOCUMENT_STORAGE_ROOT),
            'exports': Path(settings.EXPORT_STORAGE_ROOT),
            'reports': Path(settings.REPORT_STORAGE_ROOT),
            'templates': template_root()}


def find_tool(name):
    """mysqldump / mysql: MYSQL_BIN_DIR, then PATH, then the Windows default."""
    exe = f'{name}.exe' if os.name == 'nt' else name
    if settings.MYSQL_BIN_DIR:
        candidate = Path(settings.MYSQL_BIN_DIR) / exe
        if candidate.exists():
            return str(candidate)
    found = shutil.which(name)
    if found:
        return found
    if os.name == 'nt':
        matches = sorted(Path(os.environ.get('ProgramFiles', r'C:\Program Files'))
                         .glob(f'MySQL/MySQL Server */bin/{exe}'))
        if matches:
            return str(matches[-1])
    raise BackupFailed(f'{name} was not found. Install the MySQL client tools or set '
                       'MYSQL_BIN_DIR in .env.')


def _db_settings():
    db = connection.settings_dict
    return {'name': db['NAME'], 'user': db['USER'], 'password': db['PASSWORD'],
            'host': db.get('HOST') or '127.0.0.1', 'port': str(db.get('PORT') or '3306')}


def _defaults_file(directory, db):
    """A private option file so the password never appears on a command line."""
    def quote(value):
        return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'
    fd, path = tempfile.mkstemp(prefix='.my-', suffix='.cnf', dir=directory)
    with os.fdopen(fd, 'w', encoding='utf-8') as handle:
        handle.write('[client]\n'
                     f'user={quote(db["user"])}\npassword={quote(db["password"])}\n'
                     f'host={quote(db["host"])}\nport={db["port"]}\n')
    os.chmod(path, 0o600)
    return path


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b''):
            digest.update(chunk)
    return digest.hexdigest()


def project_tables():
    tables = set()
    for label in PROJECT_APPS:
        for model in apps.get_app_config(label).get_models():
            tables.add(model._meta.db_table)
    tables.add('auth_group')
    return sorted(tables)


def row_counts(cursor, tables):
    counts = {}
    for table in tables:
        cursor.execute(f'SELECT COUNT(*) FROM `{table}`')
        counts[table] = cursor.fetchone()[0]
    return counts


def applied_migrations(cursor):
    cursor.execute('SELECT app, name FROM django_migrations ORDER BY app, name')
    return [f'{app}.{name}' for app, name in cursor.fetchall()]


def _git_commit():
    try:
        result = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=settings.BASE_DIR,
                                capture_output=True, text=True, timeout=10)
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _notify_admins(title, message, key):
    from accounts.models import User
    from notifications.models import Category
    from notifications.services import notify_many
    admins = User.objects.filter(is_active=True, groups__name=roles.SYSTEM_ADMIN).distinct()
    notify_many(admins, category=Category.SYSTEM, event_key=key, title=title, message=message)


# --------------------------------------------------------------------------
# Backup
# --------------------------------------------------------------------------

def _dump_database(directory, db, target):
    tool = find_tool('mysqldump')
    cnf = _defaults_file(directory, db)
    errors = directory / 'mysqldump.err'
    try:
        with open(errors, 'wb') as err, gzip.open(target, 'wb') as out:
            process = subprocess.Popen(
                [tool, f'--defaults-extra-file={cnf}', '--single-transaction', '--quick',
                 '--routines', '--triggers', '--hex-blob', '--no-tablespaces',
                 '--set-gtid-purged=OFF', '--default-character-set=utf8mb4', db['name']],
                stdout=subprocess.PIPE, stderr=err)
            for chunk in iter(lambda: process.stdout.read(CHUNK), b''):
                out.write(chunk)
            process.stdout.close()
            code = process.wait()
        message = errors.read_text(encoding='utf-8', errors='replace').strip()
        if code != 0:
            raise BackupFailed(f'mysqldump failed (exit {code}): {message[:500]}')
    finally:
        Path(cnf).unlink(missing_ok=True)
        errors.unlink(missing_ok=True)


def _archive_files(target):
    counts = {}
    with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for area, root in file_areas().items():
            counts[area] = 0
            if not root.exists():
                continue
            for path in sorted(p for p in root.rglob('*') if p.is_file()):
                archive.write(path, f'{area}/{path.relative_to(root).as_posix()}')
                counts[area] += 1
    return counts


def create_backup(actor=None):
    """Create one backup set. Returns the BackupRun; raises BackupFailed."""
    started = timezone.now()
    set_id = f'{timezone.localtime(started):%Y%m%d-%H%M%S}-{secrets.token_hex(3)}'
    run = BackupRun.objects.create(backup_set_id=set_id, started_at=started,
                                   requested_by=actor)
    directory = backup_root() / set_id
    try:
        directory.mkdir(parents=True)
        db = _db_settings()
        tables = project_tables()
        with connection.cursor() as cursor:
            before = row_counts(cursor, tables)
        _dump_database(directory, db, directory / 'database.sql.gz')
        with connection.cursor() as cursor:
            after = row_counts(cursor, tables)
            migrations = applied_migrations(cursor)
            cursor.execute('SELECT VERSION()')
            mysql_version = cursor.fetchone()[0]
        file_counts = _archive_files(directory / 'files.zip')
        manifest = {
            'schema_version': MANIFEST_VERSION,
            'backup_set_id': set_id,
            'created_at': started.isoformat(),
            'application_commit': _git_commit(),
            'database': {'file': 'database.sql.gz', 'name': db['name'],
                         'mysql_version': mysql_version,
                         'sha256': sha256_file(directory / 'database.sql.gz'),
                         'size': (directory / 'database.sql.gz').stat().st_size},
            'files': {'file': 'files.zip', 'sha256': sha256_file(directory / 'files.zip'),
                      'size': (directory / 'files.zip').stat().st_size,
                      'counts': file_counts},
            # Counts are recorded only when nothing changed during the dump.
            'row_counts': after if before == after else None,
            'migrations': migrations,
        }
        (directory / 'manifest.json').write_text(json.dumps(manifest, indent=2),
                                                 encoding='utf-8')
        run.status = RunStatus.COMPLETED
        run.completed_at = timezone.now()
        run.manifest = manifest
        run.database_reference = f'{set_id}/database.sql.gz sha256:{manifest["database"]["sha256"][:16]}'
        run.documents_reference = f'{set_id}/files.zip documents/ ({file_counts["documents"]} files)'
        run.exports_reference = (f'{set_id}/files.zip exports/ ({file_counts["exports"]} files), '
                                 f'reports/ ({file_counts["reports"]}), '
                                 f'templates/ ({file_counts["templates"]})')
        run.save(update_fields=['status', 'completed_at', 'manifest', 'database_reference',
                                'documents_reference', 'exports_reference'])
        audit.record('backup.completed', entity=run, actor=actor,
                     changes={'backup_set_id': set_id, 'files': file_counts})
        return run
    except Exception as exc:
        shutil.rmtree(directory, ignore_errors=True)
        run.status = RunStatus.FAILED
        run.completed_at = timezone.now()
        run.error_message = f'{type(exc).__name__}: {exc}'[:4000]
        run.save(update_fields=['status', 'completed_at', 'error_message'])
        audit.record('backup.failed', entity=run, actor=actor,
                     changes={'backup_set_id': set_id, 'error': run.error_message[:500]})
        _notify_admins('Backup failed', f'Backup {set_id} failed. Open Operations to see the '
                       'error and run the backup again.', f'backup.failed:{run.pk}')
        if isinstance(exc, BackupFailed):
            raise
        raise BackupFailed(run.error_message) from exc


def verify_set(run):
    """Checksum problems of a stored backup set (empty list when intact)."""
    directory = backup_root() / run.backup_set_id
    manifest_path = directory / 'manifest.json'
    if not manifest_path.exists():
        return [f'The backup folder {directory} or its manifest is missing.']
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    problems = []
    for part in ('database', 'files'):
        path = directory / manifest[part]['file']
        if not path.exists():
            problems.append(f'{path.name} is missing.')
        elif sha256_file(path) != manifest[part]['sha256']:
            problems.append(f'{path.name} does not match its recorded checksum.')
    if manifest != run.manifest:
        problems.append('manifest.json differs from the manifest recorded in the database.')
    return problems


# --------------------------------------------------------------------------
# Restore check
# --------------------------------------------------------------------------

def restore_database_name():
    name = settings.RESTORE_CHECK_DATABASE
    if name in (connection.settings_dict['NAME'], settings.DATABASES['default']['NAME']):
        raise BackupFailed('Refusing to restore over the live database.')
    return name


def _admin_connection(db):
    import MySQLdb
    return MySQLdb.connect(host=db['host'], port=int(db['port']), user=db['user'],
                           passwd=db['password'], charset='utf8mb4')


def _load_dump(directory, db, target_name, dump_path):
    conn = _admin_connection(db)
    try:
        cur = conn.cursor()
        cur.execute(f'DROP DATABASE IF EXISTS `{target_name}`')
        cur.execute(f'CREATE DATABASE `{target_name}` CHARACTER SET utf8mb4 '
                    'COLLATE utf8mb4_0900_ai_ci')
    finally:
        conn.close()
    tool = find_tool('mysql')
    cnf = _defaults_file(directory, db)
    errors = directory / 'mysql.err'
    try:
        with open(errors, 'wb') as err, gzip.open(dump_path, 'rb') as dump:
            process = subprocess.Popen([tool, f'--defaults-extra-file={cnf}',
                                        '--default-character-set=utf8mb4', target_name],
                                       stdin=subprocess.PIPE, stderr=err)
            for chunk in iter(lambda: dump.read(CHUNK), b''):
                process.stdin.write(chunk)
            process.stdin.close()
            code = process.wait()
        if code != 0:
            raise BackupFailed('Loading the dump failed: '
                               + errors.read_text(encoding='utf-8', errors='replace')[:500])
    finally:
        Path(cnf).unlink(missing_ok=True)
        errors.unlink(missing_ok=True)


def _extract(archive_path, target):
    root = target.resolve()
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            path = (root / member.filename).resolve()
            if root not in path.parents:
                raise BackupFailed(f'Unsafe path in archive: {member.filename}')
        archive.extractall(root)


def verify_restored(cursor, files_root, manifest):
    """Checks on a restored database (DB-API cursor) and its files folder.
    Returns {'ok': bool, 'checks': [{'name', 'ok', 'detail'}]}."""
    checks = []

    def check(name, ok, detail=''):
        checks.append({'name': name, 'ok': bool(ok), 'detail': detail})

    migrations = applied_migrations(cursor)
    expected = manifest.get('migrations')
    check('Migrations match the backup', expected is None or migrations == expected,
          f'{len(migrations)} applied')
    counts = manifest.get('row_counts')
    if counts is None:
        check('Row counts', True, 'Not compared: data changed while the backup ran.')
    else:
        restored = row_counts(cursor, sorted(counts))
        differing = sorted(t for t in counts if restored.get(t) != counts[t])
        check('Row counts match the backup', not differing,
              f'{sum(restored.values())} rows in {len(counts)} tables'
              + (f'; differ: {", ".join(differing[:10])}' if differing else ''))
    for name, child, column, parent in LINKS:
        cursor.execute(f'SELECT COUNT(*) FROM `{child}` c LEFT JOIN `{parent}` p '
                       f'ON c.`{column}` = p.id WHERE c.`{column}` IS NOT NULL AND p.id IS NULL')
        orphans = cursor.fetchone()[0]
        check(f'Links: {name}', orphans == 0, f'{orphans} broken link(s)')
    for table, area in FILE_TABLES:
        cursor.execute(f'SELECT storage_key, sha256 FROM `{table}`')
        rows = cursor.fetchall()
        missing, changed = [], []
        for key, digest in rows:
            path = Path(files_root) / area / key
            if not path.is_file():
                missing.append(key)
            elif sha256_file(path) != digest:
                changed.append(key)
        check(f'Files: {table}', not missing and not changed,
              f'{len(rows)} referenced, {len(missing)} missing, {len(changed)} changed'
              + (f'; e.g. {(missing + changed)[0]}' if missing or changed else ''))
    cursor.execute('SELECT COUNT(*) FROM roster_exports WHERE is_draft = 0')
    check('Approved roster files included in the file check', True,
          f'{cursor.fetchone()[0]} official roster file(s)')
    return {'ok': all(c['ok'] for c in checks), 'checks': checks}


def restore_check(actor, run, *, keep=False):
    """Restore a backup set into the isolated database and verify it."""
    if not roles.has_role(actor, roles.SYSTEM_ADMIN):
        raise PermissionError('Only System Administrators can run restore checks.')
    target = restore_database_name()
    restore = RestoreRun.objects.create(backup_run=run, requested_by=actor,
                                        target_database=target, started_at=timezone.now())
    work = backup_root() / '_restore_check' / str(restore.pk)
    db = _db_settings()
    try:
        if run.status != RunStatus.COMPLETED:
            raise BackupFailed('Only completed backups can be restored.')
        problems = verify_set(run)
        if problems:
            raise BackupFailed(' '.join(problems))
        directory = backup_root() / run.backup_set_id
        work.mkdir(parents=True)
        _load_dump(work, db, target, directory / 'database.sql.gz')
        _extract(directory / 'files.zip', work / 'files')
        import MySQLdb
        conn = MySQLdb.connect(host=db['host'], port=int(db['port']), user=db['user'],
                               passwd=db['password'], db=target, charset='utf8mb4')
        try:
            result = verify_restored(conn.cursor(), work / 'files', run.manifest)
        finally:
            conn.close()
        result['restored_into'] = {'database': target, 'files': str(work / 'files')
                                   if keep else '(removed after the check)'}
        restore.verification_result = result
        restore.status = RunStatus.COMPLETED if result['ok'] else RunStatus.FAILED
        if not result['ok']:
            restore.error_message = '; '.join(c['name'] for c in result['checks'] if not c['ok'])
    except Exception as exc:
        restore.status = RunStatus.FAILED
        restore.error_message = f'{type(exc).__name__}: {exc}'[:4000]
    finally:
        if not keep:
            shutil.rmtree(work, ignore_errors=True)
            try:
                conn = _admin_connection(db)
                conn.cursor().execute(f'DROP DATABASE IF EXISTS `{target}`')
                conn.close()
            except Exception:
                pass
    restore.completed_at = timezone.now()
    restore.save(update_fields=['status', 'completed_at', 'verification_result',
                                'error_message'])
    audit.record('restore.checked', entity=restore, actor=actor,
                 changes={'backup_set_id': run.backup_set_id, 'status': restore.status})
    if restore.status == RunStatus.FAILED:
        _notify_admins('Restore check failed',
                       f'The restore check of backup {run.backup_set_id} failed. Open '
                       'Operations to see which checks failed.', f'restore.failed:{restore.pk}')
    return restore
