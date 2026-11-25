"""Upgrade check, step 2: run with the NEW release in `manage.py shell`, after
`migrate`. Compares the upgraded scratch database with before.json and uses
the new features on the old data. Prints UPGRADE CHECK PASSED or the problems.
See deploy/RELEASE.md, "Upgrade check".
"""
import hashlib
import json
import os
from pathlib import Path

from django.db import connection
from django.test import Client
from django.test.utils import setup_test_environment
from django.urls import reverse

setup_test_environment()
from accounts.models import User  # noqa: E402
from ai_processing import screening  # noqa: E402
from audit.models import AuditLog  # noqa: E402
from documents.models import Document, FactEvidence  # noqa: E402
from matrix import services as rosters  # noqa: E402
from matrix.models import RosterExport  # noqa: E402
from recruitment.models import Application, ApplicationStatus  # noqa: E402

work = Path(os.environ.get('UPGRADE_CHECK_DIR', '/srv/evsu-upgrade'))
before = json.loads((work / 'before.json').read_text())
problems = []
DJANGO_OWN = ('django_migrations', 'auth_permission', 'django_content_type')

tables = set(connection.introspection.table_names())
with connection.cursor() as cursor:
    for table, count in before['counts'].items():
        if table in DJANGO_OWN:
            continue  # Django adds rows for new models
        if table not in tables:
            problems.append(f'table {table} is missing')
            continue
        cursor.execute(f'SELECT COUNT(*) FROM `{table}`')
        now = cursor.fetchone()[0]
        if now != count:
            problems.append(f'{table}: {count} rows before, {now} after')
    new_tables = sorted(tables - set(before['counts']))
    for table in new_tables:
        cursor.execute(f'SELECT COUNT(*) FROM `{table}`')
        if cursor.fetchone()[0]:
            problems.append(f'new table {table} is not empty')
print('new tables:', ', '.join(new_tables) or 'none')

# Optional links added by the upgrade are empty on old records; old evidence works.
if FactEvidence.objects.exclude(extraction_item__isnull=True).exists():
    problems.append('old evidence has an AI link')
if AuditLog.objects.exclude(job__isnull=True).exists():
    problems.append('old audit entries have a job link')
if Document.objects.exclude(profile_document__isnull=True).exists():
    problems.append('old documents have a profile link')
evidence = list(FactEvidence.objects.select_related('application_fact', 'document_version'))
if not evidence:
    problems.append('no evidence links to check')
print(f'evidence links kept: {len(evidence)}')

# Roster exports: same bytes, and the official ones still download.
client = Client()
client.force_login(User.objects.get(username='head'))  # the scenario's approver
for export in RosterExport.objects.all():
    digest = hashlib.sha256(rosters.export_path(export).read_bytes()).hexdigest()
    if digest != before['exports'].get(str(export.pk)):
        problems.append(f'roster export {export.pk} changed')
    if not export.is_draft:
        response = client.get(reverse('matrix:download', args=[export.pk]))
        body = b''.join(response.streaming_content) if response.streaming else response.content
        if response.status_code != 200 or hashlib.sha256(body).hexdigest() != digest:
            problems.append(f'download of export {export.pk}: HTTP {response.status_code}')
print(f"roster exports checked: {len(before['exports'])}")

# The new features work on the upgraded data.
hired = Application.objects.get(status=ApplicationStatus.HIRED)
hr = User.objects.get(username='hr1')
check, _ = screening.run_checks(hr, hired)
print('qualification check on upgraded data:', check.overall_status)
client.force_login(hr)
if client.get(reverse('recruitment:application_review', args=[hired.pk])).status_code != 200:
    problems.append('application review page failed')

print('PROBLEMS:' if problems else 'UPGRADE CHECK PASSED')
for problem in problems:
    print(' -', problem)
