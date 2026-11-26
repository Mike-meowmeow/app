"""Check which questions the help assistant can answer, without writing answers.

    manage.py assistant_check_questions --file questions.txt [--role hr|admin]
    manage.py assistant_check_questions --saved

A questions file has one question per line. Add " => Article title" to state
which article should answer it; lines starting with # are skipped. --saved
checks the open saved questions (help guide review page) with each one's
asker role, to see which are answered now that the guide or its phrases
changed. Needs the Ollama embedding model; without it the word search is used
and the report says so.
"""

from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from accounts import roles

from ... import collection, services
from ...models import QuestionStatus, UnansweredQuestion

ROLES = {'hr': {roles.HR}, 'admin': {roles.SYSTEM_ADMIN}}


class Command(BaseCommand):
    help = 'Report which questions the help assistant can answer from the help guide.'

    def add_arguments(self, parser):
        parser.add_argument('--file', help='One question per line, optionally "=> Article title".')
        parser.add_argument('--saved', action='store_true',
                            help='Check the open saved unanswered questions.')
        parser.add_argument('--role', choices=sorted(ROLES), default='hr',
                            help='Role to ask as for --file (default hr).')

    def handle(self, *args, **options):
        if bool(options['file']) == bool(options['saved']):
            raise CommandError('Give either --file or --saved.')
        if options['saved']:
            collection.purge_expired()
            seen, items = set(), []
            for q in UnansweredQuestion.objects.filter(status=QuestionStatus.OPEN):
                if q.text.lower() in seen:
                    continue
                seen.add(q.text.lower())
                asked_as = {r.strip() for r in q.asker_role.split(',') if r.strip()} or {roles.HR}
                items.append((q.text, None, asked_as))
        else:
            path = Path(options['file'])
            if not path.exists():
                raise CommandError(f'{path} does not exist.')
            items = []
            for line in path.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                question, _, expected = line.partition('=>')
                items.append((question.strip(), expected.strip() or None, ROLES[options['role']]))
        if not items:
            self.stdout.write('No questions to check.')
            return

        answered = expected_total = expected_right = 0
        method = None
        for question, expected, user_roles in items:
            sources, nearest, similarity, how = services.match(question, user_roles)
            method = method or how
            titles = [a.title for a in sources]
            score = '  -  ' if similarity is None else f'{similarity:.2f}'
            if sources:
                answered += 1
                result = 'ANSWERED    '
            else:
                result = 'NOT ANSWERED'
            line = f'{result} {score}  {question}\n    -> {", ".join(titles) or "(closest: " + (nearest[0].title if nearest else "none") + ")"}'
            if expected:
                expected_total += 1
                if expected in titles:
                    expected_right += 1
                else:
                    line += f'\n    expected: {expected}'
            self.stdout.write(line)

        self.stdout.write('')
        if method == 'words':
            self.stdout.write(self.style.WARNING(
                'Ollama was not reachable: these results use the word search, not meaning.'))
        self.stdout.write(f'Answered: {answered} of {len(items)}.')
        if expected_total:
            self.stdout.write(f'Expected article found: {expected_right} of {expected_total}.')
        if options['saved'] and answered:
            self.stdout.write('Questions now answered can be marked on the help guide review page.')
