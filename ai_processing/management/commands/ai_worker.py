"""Background AI worker: processes queued jobs one at a time.

    manage.py ai_worker            # run until stopped (systemd service)
    manage.py ai_worker --once     # process at most one job, then exit
"""

import signal
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import close_old_connections

from ai_processing import services


class Command(BaseCommand):
    help = 'Process queued AI jobs (document text extraction).'

    def add_arguments(self, parser):
        parser.add_argument('--once', action='store_true', help='Process at most one job.')
        parser.add_argument('--sleep', type=int, default=settings.AI_WORKER_POLL_SECONDS,
                            help='Seconds to wait when no job is due.')

    def handle(self, *args, once=False, sleep=5, **options):
        self.stopping = False
        signal.signal(signal.SIGTERM, self._stop)
        signal.signal(signal.SIGINT, self._stop)
        worker = services.worker_reference()
        self.stdout.write(f'AI worker {worker} started.')
        while not self.stopping:
            close_old_connections()
            attempt = services.run_once(worker)
            if attempt is not None:
                attempt.refresh_from_db()
                job = attempt.job
                job.refresh_from_db()
                self.stdout.write(f'Job {job.pk}: attempt {attempt.attempt_number} '
                                  f'{attempt.status}; job {job.display_status}.'
                                  + (f' {attempt.error_message}' if attempt.error_message else ''))
            if once:
                break
            if attempt is None:
                for _ in range(max(sleep, 1)):
                    if self.stopping:
                        break
                    time.sleep(1)
        self.stdout.write('AI worker stopped.')

    def _stop(self, *args):
        self.stopping = True
