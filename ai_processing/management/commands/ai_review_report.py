"""How HR handled the AI's proposed items, per document type (13.3 gate).

    python manage.py ai_review_report [--csv report.csv]

Counts current (not invalidated) extraction results: items proposed, still
pending, accepted as proposed, corrected, linked, and rejected, with the
correction and rejection rates and the fields HR corrected most. These figures
describe HR's decisions on the documents processed so far; an accuracy claim
needs HR-annotated examples (see vault/AI Remaining Work).
"""

import csv

from django.core.management.base import BaseCommand

from ai_processing import services
from ai_processing.models import AIJob, JobType


def _rate(value):
    return f'{value:.0%}' if value is not None else '-'


class Command(BaseCommand):
    help = "Summarize HR's decisions on AI-proposed items per document type."

    def add_arguments(self, parser):
        parser.add_argument('--csv', help='Also write the table to this CSV file.')

    def handle(self, **options):
        csv_path = options.get('csv')
        jobs = AIJob.objects.filter(job_type=JobType.DOCUMENT_EXTRACTION,
                                    invalidated_at__isnull=True).values('pk')
        rows, totals = services.review_summary(jobs)
        if not rows:
            self.stdout.write('No extraction results yet.')
            return
        header = ('Document type', 'Proposed', 'Pending', 'Accepted', 'Corrected', 'Linked',
                  'Rejected', 'Correction rate', 'Rejection rate', 'Fields corrected most')
        table = [(r['document_type'], r['proposed'], r['pending'], r['accepted'], r['corrected'],
                  r['linked'], r['rejected'], _rate(r['correction_rate']),
                  _rate(r['rejection_rate']),
                  ', '.join(f'{f} ({n})' for f, n in r['fields']) or '-') for r in rows]
        table.append(('All', totals['proposed'], totals['pending'], totals['accepted'],
                      totals['corrected'], totals['linked'], totals['rejected'],
                      _rate(totals['correction_rate']), _rate(totals['rejection_rate']), ''))
        for row in [header] + table:
            self.stdout.write(f'{row[0]:<32} ' + ' '.join(f'{str(v):>9}' for v in row[1:9])
                              + f'  {row[9]}')
        self.stdout.write('Rates are shares of decided items (accepted, corrected, linked, '
                          'rejected). They describe HR decisions so far, not a tested accuracy.')
        if csv_path:
            with open(csv_path, 'w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle)
                writer.writerow(header)
                writer.writerows(table)
            self.stdout.write(f'Wrote {csv_path}')
