from datetime import datetime, timedelta, timezone as dt_timezone

from django.core.management.base import BaseCommand

from analytics.models import AnalyticsReport
from audit import services as audit
from documents.models import DocumentVersion
from matrix.models import RosterExport, RosterTemplate
from operations.backup import file_areas

REFERENCES = {'documents': DocumentVersion, 'exports': RosterExport,
              'reports': AnalyticsReport, 'templates': RosterTemplate}


class Command(BaseCommand):
    help = ('List private files that no database record references (left behind by an '
            'interrupted upload or export). Only files older than --min-age-hours are '
            'considered, so work in progress is never touched. Add --delete to remove them.')

    def add_arguments(self, parser):
        parser.add_argument('--min-age-hours', type=int, default=24)
        parser.add_argument('--delete', action='store_true')

    def handle(self, *args, **options):
        cutoff = datetime.now(dt_timezone.utc) - timedelta(hours=options['min_age_hours'])
        found = []
        for area, root in file_areas().items():
            if not root.exists():
                continue
            known = set(REFERENCES[area].objects.values_list('storage_key', flat=True))
            for path in root.rglob('*'):
                if not path.is_file() or path.name.startswith('.'):
                    continue
                key = path.relative_to(root).as_posix()
                modified = datetime.fromtimestamp(path.stat().st_mtime, dt_timezone.utc)
                if key not in known and modified < cutoff:
                    found.append((area, key, path))
        for area, key, path in found:
            self.stdout.write(f'{area}/{key}')
            if options['delete']:
                path.unlink(missing_ok=True)
        verb = 'Deleted' if options['delete'] else 'Found'
        self.stdout.write(self.style.SUCCESS(f'{verb} {len(found)} unreferenced file(s).'))
        if options['delete'] and found:
            audit.record('files.orphans_deleted', actor=None, entity_type='files',
                         changes={'count': len(found),
                                  'files': [f'{a}/{k}' for a, k, _ in found[:100]]})
