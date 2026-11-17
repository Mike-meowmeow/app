from django.core.management.base import BaseCommand, CommandError

from accounts.models import User
from operations import backup
from operations.models import BackupRun, RunStatus


class Command(BaseCommand):
    help = ('Restore a backup set into the isolated <DB_NAME>_restore database and a scratch '
            'folder, verify it, and record the result. Never touches the live database.')

    def add_arguments(self, parser):
        parser.add_argument('backup_set', nargs='?', default='latest',
                            help='backup set id, or "latest" (default)')
        parser.add_argument('--requested-by', required=True,
                            help='username of the System Administrator running the check')
        parser.add_argument('--keep', action='store_true',
                            help='keep the restored database and files for inspection')

    def handle(self, *args, **options):
        actor = User.objects.filter(username=options['requested_by']).first()
        if actor is None:
            raise CommandError(f'No user named {options["requested_by"]!r}.')
        runs = BackupRun.objects.filter(status=RunStatus.COMPLETED)
        run = runs.first() if options['backup_set'] == 'latest' else \
            runs.filter(backup_set_id=options['backup_set']).first()
        if run is None:
            raise CommandError('No completed backup set found.')
        try:
            restore = backup.restore_check(actor, run, keep=options['keep'])
        except PermissionError as exc:
            raise CommandError(str(exc))
        for check in restore.verification_result.get('checks', []):
            mark = self.style.SUCCESS('PASS') if check['ok'] else self.style.ERROR('FAIL')
            self.stdout.write(f'  {mark}  {check["name"]}: {check["detail"]}')
        if restore.status == RunStatus.COMPLETED:
            self.stdout.write(self.style.SUCCESS(f'Restore check of {run.backup_set_id} passed.'))
        else:
            raise CommandError(f'Restore check failed: {restore.error_message}')
