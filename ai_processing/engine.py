"""Reading text from document files (13.2): digital PDF text first, OCR for
scanned pages and images.

The heavy packages (pdfplumber, pypdfium2, pytesseract, spaCy) are imported only
when a document is processed, so the web processes and the development
computers do not need them. Without them, processing fails with
EngineUnavailable and HR continues manually (FR-024).
"""

from functools import lru_cache

from django.conf import settings


class EngineUnavailable(Exception):
    """The AI runtime is not installed where the worker runs."""


class ExtractionError(Exception):
    """The file could not be read (damaged, encrypted, unsupported)."""


def _require(module):
    try:
        return __import__(module)
    except ImportError as exc:
        raise EngineUnavailable(f'The AI runtime is not installed here ({module} is missing). '
                                'See deploy/AI-SERVER.md.') from exc


def configuration():
    """Settings that change the result; part of each job's identity."""
    return {'ocr_dpi': settings.AI_OCR_DPI, 'ocr_languages': settings.AI_OCR_LANGUAGES,
            'max_pages': settings.AI_MAX_PAGES,
            'min_page_characters': settings.AI_MIN_PAGE_CHARACTERS}


def versions():
    """Versions of the tools that produced a result (recorded with it)."""
    from importlib.metadata import PackageNotFoundError, version
    found = {}
    for name in ('pdfplumber', 'pypdfium2', 'pytesseract', 'spacy'):
        try:
            found[name] = version(name)
        except PackageNotFoundError:
            found[name] = None
    try:
        import pytesseract
        found['tesseract'] = str(pytesseract.get_tesseract_version())
    except Exception:  # missing package or program
        found['tesseract'] = None
    nlp = load_nlp()
    found['spacy_model'] = f"{nlp.meta.get('name')} {nlp.meta.get('version')}" if nlp else None
    return {k: str(v) if v is not None else None for k, v in found.items()}


@lru_cache(maxsize=1)
def load_nlp():
    """The spaCy model, loaded once per worker; None when unavailable."""
    try:
        import spacy
        return spacy.load(settings.SPACY_MODEL)
    except Exception:
        return None


def _ocr(image, config):
    pytesseract = _require('pytesseract')
    data = pytesseract.image_to_data(image.convert('L'), lang=config['ocr_languages'],
                                     output_type=pytesseract.Output.DICT)
    confidences = [float(c) for c, word in zip(data['conf'], data['text'])
                   if word.strip() and float(c) >= 0]
    text = pytesseract.image_to_string(image.convert('L'), lang=config['ocr_languages'])
    confidence = round(sum(confidences) / len(confidences) / 100, 3) if confidences else 0.0
    return text, confidence


def extract_pages(path, mime_type, config=None, heartbeat=lambda: None):
    """[{'page', 'method', 'text', 'characters', 'ocr_confidence'}] for a file."""
    config = config or configuration()
    pages = []
    if mime_type == 'application/pdf':
        pdfplumber = _require('pdfplumber')
        pdfium = _require('pypdfium2')
        try:
            pdf = pdfplumber.open(str(path))
        except Exception as exc:
            raise ExtractionError(f'The PDF could not be opened: {exc}') from exc
        with pdf:
            document = None
            for index, page in enumerate(pdf.pages[:config['max_pages']], start=1):
                text = page.extract_text() or ''
                entry = {'page': index, 'method': 'text', 'text': text,
                         'ocr_confidence': None}
                if len(text.strip()) < config['min_page_characters']:
                    document = document or pdfium.PdfDocument(str(path))
                    image = document[index - 1].render(scale=config['ocr_dpi'] / 72).to_pil()
                    entry['text'], entry['ocr_confidence'] = _ocr(image, config)
                    entry['method'] = 'ocr'
                entry['characters'] = len(entry['text'].strip())
                pages.append(entry)
                heartbeat()
            if len(pdf.pages) > config['max_pages']:
                pages.append({'page': None, 'method': 'skipped', 'text': '',
                              'characters': 0, 'ocr_confidence': None,
                              'note': f'Only the first {config["max_pages"]} pages were read.'})
    elif mime_type in ('image/jpeg', 'image/png'):
        _require('PIL')
        from PIL import Image
        try:
            image = Image.open(str(path))
            image.load()
        except Exception as exc:
            raise ExtractionError(f'The image could not be opened: {exc}') from exc
        text, confidence = _ocr(image, config)
        pages.append({'page': 1, 'method': 'ocr', 'text': text,
                      'characters': len(text.strip()), 'ocr_confidence': confidence})
        heartbeat()
    else:
        raise ExtractionError(f'Unsupported file type: {mime_type}')
    return pages
