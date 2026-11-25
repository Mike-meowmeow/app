"""Run the extraction on a file without the database (for evaluation).

    manage.py ai_extract_file sample.pdf --type TOR
    manage.py ai_extract_file sample.pdf --type TRAINING --text

Prints the proposed items as JSON. Use only made-up or approved test files.
"""

import json
import mimetypes
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from ai_processing import engine, parsers


class Command(BaseCommand):
    help = 'Extract text and propose items from one file (no database changes).'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--type', required=True,
                            help='Document type code, e.g. TOR, DIPLOMA, TRAINING, PRC.')
        parser.add_argument('--text', action='store_true', help='Also print the page text.')

    def handle(self, path, type, text=False, **options):
        file = Path(path)
        if not file.is_file():
            raise CommandError(f'No such file: {file}')
        mime = mimetypes.guess_type(file.name)[0]
        try:
            pages = engine.extract_pages(file, mime)
        except (engine.EngineUnavailable, engine.ExtractionError) as exc:
            raise CommandError(str(exc))
        items, warnings = parsers.propose(type.upper(), pages, engine.load_nlp())
        result = {'file': file.name, 'document_type': type.upper(),
                  'pages': [{k: v for k, v in p.items() if text or k != 'text'} for p in pages],
                  'warnings': warnings, 'items': items}
        self.stdout.write(json.dumps(result, indent=2, ensure_ascii=False))
