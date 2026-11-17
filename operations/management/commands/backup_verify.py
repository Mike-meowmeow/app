from django.core.management.base import BaseCommand, CommandError

from operations import backup
from operations.models import BackupRun


class Command(BaseCommand):
    help = 'Check that stored backup sets still match their recorded checksums.'

    def add_arguments(self, parser):
        parser.add_argument('backup_set', nargs='?', help='backup set id (default: all)')

    def handle(self, *args, **options):
        runs = BackupRun.objects.filter(status='completed')
        if options['backup_set']:
            runs = runs.filter(backup_set_id=options['backup_set'])
        failed = 0
        for run in runs:
            problems = backup.verify_set(run)
            if problems:
                failed += 1
                self.stdout.write(self.style.ERROR(f'{run.backup_set_id}: {" ".join(problems)}'))
            else:
                self.stdout.write(self.style.SUCCESS(f'{run.backup_set_id}: intact'))
        if failed:
            raise CommandError(f'{failed} backup set(s) failed verification.')
