"""Stage 9 tests: backup sets and restore verification (FR-020), audit search
and export (FR-017), login throttling and proxy-aware client addresses
(FR-001/FR-017), protected file delivery, and the orphan-file sweep."""

import json
import os
import shutil
import tempfile
import time
import unittest
import zipfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import roles
from accounts.models import User
from accounts.test_stage3 import PASSWORD, make_user, seed
from audit.context import client_ip
from audit.models import AuditLog
from core.files import private_file_response
from documents.models import DocumentVersion
from notifications.models import Notification
from recruitment.test_applications import ApplicationTestCase

from . import backup
from .models import BackupRun, RestoreRun, RunStatus


def _tool_available(name):
    try:
        backup.find_tool(name)
        return True
    except backup.BackupFailed:
        return False


def _restore_grant():
    with connection.cursor() as cursor:
        cursor.execute('SHOW GRANTS FOR CURRENT_USER()')
        grants = [row[0].replace('\\', '') for row in cursor.fetchall()]
    target = f'`{settings.RESTORE_CHECK_DATABASE}`'
    return any(target in g or ('ON *.*' in g and 'ALL PRIVILEGES' in g) for g in grants)


# ==========================================================================
# Restore verification logic (runs against the test database itself)
# ==========================================================================

class VerifyRestoredTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.files = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.files, ignore_errors=True)
        shutil.copytree(self.storage_dir, self.files / 'documents')
        with connection.cursor() as cursor:
            self.manifest = {'migrations': backup.applied_migrations(cursor),
                             'row_counts': backup.row_counts(cursor, backup.project_tables())}

    def result(self):
        with connection.cursor() as cursor:
            return backup.verify_restored(cursor, self.files, self.manifest)

    def failing(self, result):
        return [c['name'] for c in result['checks'] if not c['ok']]

    def test_intact_restore_passes(self):
        result = self.result()
        self.assertTrue(result['ok'], self.failing(result))
        names = [c['name'] for c in result['checks']]
        self.assertIn('Files: document_versions', names)
        self.assertIn('Links: applications -> applicants', names)

    def test_missing_or_changed_file_fails(self):
        version = DocumentVersion.objects.first()
        path = self.files / 'documents' / version.storage_key
        path.write_bytes(b'tampered')
        self.assertEqual(self.failing(self.result()), ['Files: document_versions'])
        path.unlink()
        detail = next(c['detail'] for c in self.result()['checks']
                      if c['name'] == 'Files: document_versions')
        self.assertIn('1 missing', detail)

    def test_row_count_and_migration_differences_fail(self):
        self.manifest['row_counts']['applications'] += 1
        self.manifest['migrations'] = self.manifest['migrations'][:-1]
        self.assertEqual(set(self.failing(self.result())),
                         {'Row counts match the backup', 'Migrations match the backup'})
        self.manifest['row_counts'] = None  # data changed during the backup: not compared
        self.manifest['migrations'] = None
        self.assertTrue(self.result()['ok'])


# ==========================================================================
# Real backup and restore (needs mysqldump; restore needs the restore grant)
# ==========================================================================

@unittest.skipUnless(_tool_available('mysqldump'), 'mysqldump is not installed')
class BackupIntegrationTests(TransactionTestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        private = self.root / 'private'
        (private / 'documents' / '2026').mkdir(parents=True)
        (private / 'documents' / '2026' / 'a.pdf').write_bytes(b'%PDF-1.4 sample')
        override = override_settings(
            BACKUP_ROOT=self.root / 'backups', PRIVATE_STORAGE_ROOT=private,
            DOCUMENT_STORAGE_ROOT=private / 'documents', EXPORT_STORAGE_ROOT=private / 'exports',
            REPORT_STORAGE_ROOT=private / 'reports')
        override.enable()
        self.addCleanup(override.disable)
        seed()
        self.admin = make_user('sysadmin', roles.SYSTEM_ADMIN)

    def test_backup_set_is_complete_and_verifiable(self):
        run = backup.create_backup(self.admin)
        self.assertEqual(run.status, RunStatus.COMPLETED)
        directory = self.root / 'backups' / run.backup_set_id
        self.assertTrue((directory / 'database.sql.gz').stat().st_size > 0)
        with zipfile.ZipFile(directory / 'files.zip') as archive:
            self.assertIn('documents/2026/a.pdf', archive.namelist())
        manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest, run.manifest)
        self.assertEqual(manifest['files']['counts']['documents'], 1)
        self.assertIn('accounts.0001_initial', manifest['migrations'])
        self.assertEqual(backup.verify_set(run), [])
        self.assertFalse([p for p in directory.iterdir() if p.suffix == '.cnf'])  # no password file
        with open(directory / 'files.zip', 'ab') as handle:
            handle.write(b'corruption')
        self.assertTrue(any('checksum' in p for p in backup.verify_set(run)))
        out = StringIO()
        with self.assertRaises(Exception):
            call_command('backup_verify', stdout=out)

    @unittest.skipUnless(_tool_available('mysql'), 'mysql client is not installed')
    def test_restore_check_into_isolated_database(self):
        if not _restore_grant():
            self.skipTest(f'No access to {settings.RESTORE_CHECK_DATABASE}; run setup_db.py.')
        run = backup.create_backup(self.admin)
        restore = backup.restore_check(self.admin, run)
        self.assertEqual(restore.status, RunStatus.COMPLETED, restore.error_message)
        self.assertTrue(restore.verification_result['ok'])
        self.assertEqual(restore.target_database, settings.RESTORE_CHECK_DATABASE)
        self.assertNotEqual(restore.target_database, connection.settings_dict['NAME'])


class BackupFailureTests(TestCase):
    def setUp(self):
        seed()
        self.admin = make_user('sysadmin', roles.SYSTEM_ADMIN)
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        override = override_settings(BACKUP_ROOT=self.root)
        override.enable()
        self.addCleanup(override.disable)

    def test_failure_is_recorded_and_admins_notified(self):
        with mock.patch('operations.backup.find_tool',
                        side_effect=backup.BackupFailed('mysqldump was not found.')):
            with self.assertRaises(backup.BackupFailed):
                backup.create_backup()
        run = BackupRun.objects.get()
        self.assertEqual(run.status, RunStatus.FAILED)
        self.assertIn('mysqldump was not found', run.error_message)
        self.assertEqual(list(self.root.iterdir()), [])  # partial set removed
        self.assertTrue(Notification.objects.filter(user=self.admin,
                                                    title='Backup failed').exists())
        self.assertTrue(AuditLog.objects.filter(action='backup.failed').exists())

    def test_restore_requires_system_administrator(self):
        run = BackupRun.objects.create(backup_set_id='x', status=RunStatus.COMPLETED,
                                       started_at=timezone.now())
        hr = make_user('hr9', roles.HR)
        with self.assertRaises(PermissionError):
            backup.restore_check(hr, run)
        self.assertFalse(RestoreRun.objects.exists())

    def test_restore_of_damaged_set_fails_safely(self):
        run = BackupRun.objects.create(backup_set_id='missing-set', status=RunStatus.COMPLETED,
                                       started_at=timezone.now(), manifest={'x': 1})
        with mock.patch('operations.backup._admin_connection'):
            restore = backup.restore_check(self.admin, run)
        self.assertEqual(restore.status, RunStatus.FAILED)
        self.assertIn('missing', restore.error_message)
        self.assertTrue(Notification.objects.filter(title='Restore check failed').exists())

    def test_monitoring_page(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse('operations:overview'))
        self.assertContains(response, 'No successful backup has been recorded')
        BackupRun.objects.create(backup_set_id='ok', status=RunStatus.COMPLETED,
                                 started_at=timezone.now(), completed_at=timezone.now())
        self.assertContains(self.client.get(reverse('operations:overview')),
                            'Last successful backup: ok')
        self.client.force_login(make_user('appr', roles.APPROVER))
        self.assertEqual(self.client.get(reverse('operations:overview')).status_code, 403)


# ==========================================================================
# Security hardening
# ==========================================================================

class LoginThrottleTests(TestCase):
    def setUp(self):
        seed()
        self.user = make_user('rosa', roles.HR)
        self.other = make_user('ben', roles.HR)

    def login(self, username, password, ip='10.0.0.5'):
        return self.client.post(reverse('accounts:login'),
                                {'username': username, 'password': password},
                                REMOTE_ADDR=ip)

    @override_settings(LOGIN_MAX_FAILURES=3, LOGIN_MAX_FAILURES_PER_IP=6)
    def test_repeated_failures_lock_the_identifier(self):
        for _ in range(3):
            self.assertContains(self.login('rosa', 'wrong'), 'Incorrect username/email')
        response = self.login('rosa', PASSWORD)  # correct password is refused while locked
        self.assertContains(response, 'Too many failed sign-in attempts')
        self.assertNotIn('_auth_user_id', self.client.session)
        response = self.login('nobody-here', 'wrong')  # unknown accounts behave the same
        self.assertEqual(self.login('ben', PASSWORD, ip='10.0.0.9').status_code, 302)

    @override_settings(LOGIN_MAX_FAILURES=50, LOGIN_MAX_FAILURES_PER_IP=4)
    def test_failures_from_one_address_lock_that_address(self):
        for name in ('a1', 'a2', 'a3', 'a4'):
            self.login(name, 'wrong', ip='10.9.9.9')
        self.assertContains(self.login('ben', PASSWORD, ip='10.9.9.9'), 'Too many failed')
        self.assertEqual(self.login('ben', PASSWORD, ip='10.1.1.1').status_code, 302)

    @override_settings(LOGIN_MAX_FAILURES=2, LOGIN_LOCKOUT_MINUTES=15)
    def test_lock_expires_after_the_window(self):
        self.login('rosa', 'wrong')
        self.login('rosa', 'wrong')
        AuditLog.objects.filter(action='auth.login_failed').update(
            occurred_at=timezone.now() - timedelta(minutes=16))
        self.assertEqual(self.login('rosa', PASSWORD).status_code, 302)


class ClientAddressTests(TestCase):
    def request(self, remote, forwarded=None):
        meta = {'REMOTE_ADDR': remote}
        if forwarded:
            meta['HTTP_X_FORWARDED_FOR'] = forwarded
        return RequestFactory().get('/', **meta)

    @override_settings(TRUSTED_PROXIES=['127.0.0.1'])
    def test_forwarded_address_only_from_trusted_proxy(self):
        self.assertEqual(client_ip(self.request('127.0.0.1', '203.0.113.7')), '203.0.113.7')
        # A client cannot spoof by prepending: the right-most untrusted entry wins.
        self.assertEqual(client_ip(self.request('127.0.0.1', '1.1.1.1, 203.0.113.7')),
                         '203.0.113.7')
        self.assertEqual(client_ip(self.request('198.51.100.2', '203.0.113.7')),
                         '198.51.100.2')
        self.assertEqual(client_ip(self.request('127.0.0.1', 'not-an-ip')), '127.0.0.1')

    def test_without_trusted_proxies_header_is_ignored(self):
        self.assertEqual(client_ip(self.request('198.51.100.2', '203.0.113.7')),
                         '198.51.100.2')


class ProtectedDeliveryTests(TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        (self.root / 'documents').mkdir()
        self.file = self.root / 'documents' / 'a b.pdf'
        self.file.write_bytes(b'%PDF-1.4')

    def test_nginx_mode_sends_internal_redirect_only(self):
        with override_settings(PRIVATE_FILE_DELIVERY='nginx', PRIVATE_STORAGE_ROOT=self.root):
            response = private_file_response(self.file, filename='Résumé.pdf',
                                             content_type='application/pdf')
        self.assertEqual(response['X-Accel-Redirect'], '/protected/documents/a%20b.pdf')
        self.assertEqual(response.content, b'')
        self.assertIn('attachment', response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_nginx_mode_refuses_files_outside_private_storage(self):
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        (outside / 'x.txt').write_text('x')
        from django.core.exceptions import PermissionDenied
        with override_settings(PRIVATE_FILE_DELIVERY='nginx', PRIVATE_STORAGE_ROOT=self.root):
            with self.assertRaises(PermissionDenied):
                private_file_response(outside / 'x.txt', filename='x.txt')

    def test_django_mode_streams_file(self):
        response = private_file_response(self.file, filename='a.pdf')
        self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4')
        self.assertNotIn('X-Accel-Redirect', response)


# ==========================================================================
# Audit search/export and orphan sweep
# ==========================================================================

class AuditTrailTests(TestCase):
    def setUp(self):
        seed()
        self.approver = make_user('head', roles.APPROVER)
        from audit import services as audit
        audit.record('roster.approved', actor=self.approver, entity_type='matrix.ApplicantRoster',
                     entity_id='abc', reason='=HYPERLINK("http://evil")')
        audit.record('auth.login_failed', actor=None, entity_type='auth',
                     changes={'identifier': 'x'})

    def test_search_and_export(self):
        self.client.force_login(self.approver)
        response = self.client.get(reverse('audit:list'), {'action': 'roster.'})
        self.assertEqual([log.action for log in response.context['page']], ['roster.approved'])
        response = self.client.get(reverse('audit:list'), {'action': 'roster.', 'export': 'csv'})
        body = response.content.decode('utf-8-sig')
        self.assertIn('roster.approved', body)
        self.assertIn("'=HYPERLINK", body)  # formula neutralized
        self.assertNotIn('auth.login_failed', body)
        self.assertTrue(AuditLog.objects.filter(action='audit.exported').exists())

    def test_only_auditors(self):
        self.client.force_login(make_user('hrx', roles.HR))
        self.assertEqual(self.client.get(reverse('audit:list')).status_code, 403)
        self.client.force_login(make_user('admin', roles.SYSTEM_ADMIN))
        self.assertEqual(self.client.get(reverse('audit:list')).status_code, 200)


class OrphanSweepTests(ApplicationTestCase):
    def test_lists_and_deletes_only_old_unreferenced_files(self):
        self.submitted()
        referenced = [p for p in Path(self.storage_dir).rglob('*') if p.is_file()]
        orphan = Path(self.storage_dir) / 'old' / 'orphan.pdf'
        orphan.parent.mkdir(parents=True)
        orphan.write_bytes(b'x')
        fresh = Path(self.storage_dir) / 'old' / 'fresh.pdf'
        fresh.write_bytes(b'y')
        old = time.time() - 3 * 86400
        os.utime(orphan, (old, old))
        for path in referenced:
            os.utime(path, (old, old))
        out = StringIO()
        call_command('sweep_orphan_files', stdout=out)
        self.assertIn('documents/old/orphan.pdf', out.getvalue())
        self.assertNotIn('fresh.pdf', out.getvalue())
        self.assertTrue(orphan.exists())
        call_command('sweep_orphan_files', '--delete', stdout=StringIO())
        self.assertFalse(orphan.exists())
        self.assertTrue(fresh.exists())
        self.assertTrue(all(p.exists() for p in referenced))
