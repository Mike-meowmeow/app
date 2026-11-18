"""Test runner that keeps every test away from the real private storage.

Tests run against an empty test database, so to them every real file under
private/ and backups/ looks unreferenced. Without this isolation a test that
sweeps, restores or writes files could change or delete development data.
All storage and backup roots are pointed at one temporary directory for the
whole run; individual tests may still override them further.
"""

import shutil
import tempfile
from pathlib import Path

from django.conf import settings
from django.test.runner import DiscoverRunner


class IsolatedStorageRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        self._storage_tmp = Path(tempfile.mkdtemp(prefix='evsu-test-storage-'))
        private = self._storage_tmp / 'private'
        self._saved = {name: getattr(settings, name) for name in (
            'PRIVATE_STORAGE_ROOT', 'DOCUMENT_STORAGE_ROOT', 'EXPORT_STORAGE_ROOT',
            'REPORT_STORAGE_ROOT', 'BACKUP_ROOT')}
        settings.PRIVATE_STORAGE_ROOT = private
        settings.DOCUMENT_STORAGE_ROOT = private / 'documents'
        settings.EXPORT_STORAGE_ROOT = private / 'exports'
        settings.REPORT_STORAGE_ROOT = private / 'reports'
        settings.BACKUP_ROOT = self._storage_tmp / 'backups'

    def teardown_test_environment(self, **kwargs):
        for name, value in self._saved.items():
            setattr(settings, name, value)
        shutil.rmtree(self._storage_tmp, ignore_errors=True)
        super().teardown_test_environment(**kwargs)
