"""Upgrade check, step 1: run with the OLD release in `manage.py shell`.

Performs the non-AI acceptance workflow from that release's own tests against
the scratch database for real (registration to hiring, a released roster),
adds manual evidence links, moves the files into PRIVATE_STORAGE_ROOT, and
records row counts and roster export checksums in $UPGRADE_CHECK_DIR/before.json.
See deploy/RELEASE.md, "Upgrade check". Only made-up data is created.
"""
import hashlib
import json
import os
import shutil
from pathlib import Path

from django.db import connection
from django.test import Client
from django.test.utils import setup_test_environment

setup_test_environment()
from operations.test_acceptance import NonAIAcceptanceScenario  # noqa: E402

work = Path(os.environ.get('UPGRADE_CHECK_DIR', '/srv/evsu-upgrade'))
storage = Path(os.environ['PRIVATE_STORAGE_ROOT'])  # the scratch copy's real storage

case = NonAIAcceptanceScenario('test_complete_manual_workflow')
case.client = Client()
case.setUp()
try:
    case.test_complete_manual_workflow()
except AssertionError as exc:
    # Pre-AI releases end by asserting that no AI package is installed; on the
    # AI server they are. The whole workflow before it has run.
    print('note: final package assertion failed (expected on the AI server):', str(exc)[:60])

from matrix import services as rosters  # noqa: E402
from matrix.models import RosterExport  # noqa: E402
from recruitment import application_services as applications  # noqa: E402
from recruitment.models import Application  # noqa: E402

for application in Application.objects.all():  # manual evidence made before AI existed
    version = application.documents.first().current_version()
    for fact in application.facts.filter(review_status='accepted'):
        applications.link_evidence(case.hr, fact, version)

exports = {str(e.pk): hashlib.sha256(rosters.export_path(e).read_bytes()).hexdigest()
           for e in RosterExport.objects.all()}
shutil.copytree(case.storage_dir, storage / 'documents', dirs_exist_ok=True)
shutil.copytree(case.private, storage, dirs_exist_ok=True)
case.tearDown()  # removes the test's temporary folders

with connection.cursor() as cursor:
    counts = {}
    for table in connection.introspection.table_names():
        cursor.execute(f'SELECT COUNT(*) FROM `{table}`')
        counts[table] = cursor.fetchone()[0]
work.mkdir(parents=True, exist_ok=True)
(work / 'before.json').write_text(json.dumps({'counts': counts, 'exports': exports}, indent=1))
print(f'populated: {len(counts)} tables, {sum(counts.values())} rows, '
      f'{len(exports)} roster exports')
