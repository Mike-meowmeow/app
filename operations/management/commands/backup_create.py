from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from operations import backup


class Command(BaseCommand):
    help = ('Create a consistent backup set (database dump, then private files) under '
            'BACKUP_ROOT and record it. Schedule this daily on the server.')

    def add_arguments(self, parser):
        parser.add_argument('--requested-by', help='username recorded as the requester')

    def handle(self, *args, **options):
        actor = None
        if options['requested_by']:
            actor = User.objects.filter(username=options['requested_by']).first()
            if actor is None:
                raise CommandError(f'No user named {options["requested_by"]!r}.')
        try:
            run = backup.create_backup(actor)
        except backup.BackupFailed as exc:
            raise CommandError(f'Backup failed: {exc}')
        files = run.manifest['files']['counts']
        self.stdout.write(self.style.SUCCESS(
            f'Backup {run.backup_set_id} completed: database '
            f'{run.manifest["database"]["size"] // 1024} KB, files {files}.'))
        self.stdout.write(f'Location: {backup.backup_root() / run.backup_set_id}')
