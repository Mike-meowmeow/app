"""Compare SBERT similarities with HR judgments (13.5 gate).

Input: a CSV file with the columns `competency`, `qualification`, and
`hr_judgment` (`related` or `not_related`), one pair per row, written by HR
from anonymized or made-up examples. The command reports each pair's
similarity, the average for each HR judgment, and how often a related pair
scores above a not-related pair (1.0 = always, 0.5 = no better than chance).

    python manage.py evaluate_competency_matching pairs.csv [--out results.csv]

Runs where the SBERT model is installed (the AI server). Nothing is stored in
the database.
"""

import csv
from itertools import product
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from ai_processing import engine
from ai_processing.matching import cosine

RELATED = {'related', 'yes', 'relevant', '1', 'true'}
NOT_RELATED = {'not_related', 'not related', 'no', 'not_relevant', 'not relevant', '0', 'false'}


class Command(BaseCommand):
    help = 'Compare SBERT competency similarities with HR judgments from a CSV file.'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--out', help='Also write each pair with its similarity to this CSV.')

    def handle(self, path, out=None, **options):
        rows = self.read(Path(path))
        try:
            vectors = engine.embed([t for r in rows for t in (r['competency'],
                                                               r['qualification'])])
            identity = engine.sbert_identity()
        except engine.EngineUnavailable as exc:
            raise CommandError(str(exc)) from exc
        for i, row in enumerate(rows):
            row['similarity'] = round(cosine(vectors[2 * i], vectors[2 * i + 1]), 4)

        self.stdout.write(f"Model: {identity.get('model')} "
                          f"(revision {identity.get('revision') or 'unknown'}, "
                          f"sentence-transformers {identity.get('sentence-transformers')})")
        self.stdout.write(f'Pairs: {len(rows)}\n')
        for row in sorted(rows, key=lambda r: -r['similarity']):
            self.stdout.write(f"{row['similarity']:+.2f}  {row['label']:<11}  "
                              f"{row['competency'][:40]!r} vs {row['qualification'][:50]!r}")
        related = [r['similarity'] for r in rows if r['label'] == 'related']
        unrelated = [r['similarity'] for r in rows if r['label'] == 'not_related']
        self.stdout.write('')
        for name, values in (('related', related), ('not related', unrelated)):
            if values:
                self.stdout.write(f'Average similarity, HR said {name}: '
                                  f'{sum(values) / len(values):+.3f} ({len(values)} pairs, '
                                  f'range {min(values):+.2f} to {max(values):+.2f})')
        if related and unrelated:
            pairs = list(product(related, unrelated))
            ordered = sum(1.0 if a > b else 0.5 if a == b else 0.0 for a, b in pairs) / len(pairs)
            self.stdout.write(f'Related pair scored above a not-related pair: {ordered:.0%} '
                              f'of {len(pairs)} comparisons (50% = chance).')
        else:
            self.stdout.write('Add both related and not-related pairs to compare them.')
        self.stdout.write('These figures describe this sample only; they are not an accuracy '
                          'claim for other vacancies.')
        if out:
            with open(out, 'w', newline='', encoding='utf-8') as handle:
                writer = csv.writer(handle)
                writer.writerow(['competency', 'qualification', 'hr_judgment', 'similarity',
                                 'model'])
                for row in rows:
                    writer.writerow([row['competency'], row['qualification'], row['label'],
                                     row['similarity'], settings.SBERT_MODEL])
            self.stdout.write(f'Wrote {out}')

    def read(self, path):
        if not path.exists():
            raise CommandError(f'{path} does not exist.')
        with open(path, newline='', encoding='utf-8-sig') as handle:
            reader = csv.DictReader(handle)
            missing = {'competency', 'qualification', 'hr_judgment'} - set(reader.fieldnames or [])
            if missing:
                raise CommandError(f'Missing columns: {", ".join(sorted(missing))}.')
            rows = []
            for number, row in enumerate(reader, start=2):
                judgment = (row['hr_judgment'] or '').strip().lower()
                if judgment in RELATED:
                    label = 'related'
                elif judgment in NOT_RELATED:
                    label = 'not_related'
                else:
                    raise CommandError(f'Line {number}: hr_judgment must be related or '
                                       'not_related.')
                competency, qualification = (row['competency'] or '').strip(), \
                    (row['qualification'] or '').strip()
                if not competency or not qualification:
                    raise CommandError(f'Line {number}: competency and qualification are needed.')
                rows.append({'competency': competency, 'qualification': qualification,
                             'label': label})
        if not rows:
            raise CommandError('The file has no pairs.')
        return rows
