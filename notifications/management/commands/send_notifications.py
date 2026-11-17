from django.core.management.base import BaseCommand

from notifications import services


class Command(BaseCommand):
    help = ('Send email copies that are still owed (never attempted, or failed fewer times '
            'than NOTIFICATION_MAX_ATTEMPTS). Safe to run repeatedly, e.g. every few minutes.')

    def handle(self, *args, **options):
        sent, failed = services.retry_pending()
        self.stdout.write(self.style.SUCCESS(f'Sent: {sent}. Failed: {failed}.'))
