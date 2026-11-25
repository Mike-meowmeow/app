"""Check that the AI server's runtime works (deploy/AI-SERVER.md, step 6).

Run on the server from the project folder, after installing requirements-ai.txt:

    sudo -u evsu .venv/bin/python deploy/ai_check.py

It uses only made-up sample text: no database, no applicant files, and no
internet access once the models are downloaded. Each check prints PASS or FAIL;
the exit code is 0 only when all of them pass. This is not part of the Django
test suite, because the development computers do not install the AI packages.
"""

import io
import os
import platform
import sys
import time
from pathlib import Path

SAMPLE = 'Bachelor of Secondary Education major in Mathematics'
RESULTS = []


def check(name):
    def wrap(fn):
        def run():
            start = time.perf_counter()
            try:
                detail = fn() or ''
                ok = True
            except Exception as exc:  # report every failure, keep going
                detail, ok = f'{type(exc).__name__}: {exc}', False
            seconds = time.perf_counter() - start
            RESULTS.append(ok)
            print(f"{'PASS' if ok else 'FAIL'}  {name} ({seconds:.1f} s){' - ' + detail if detail else ''}",
                  flush=True)
        return run
    return wrap


def load_env():
    env_file = Path('.env')
    if env_file.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_file)
        except ImportError:
            pass


def sample_image(text=SAMPLE):
    from PIL import Image, ImageDraw, ImageFont
    font_path = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    font = (ImageFont.truetype(str(font_path), 40) if font_path.exists()
            else ImageFont.load_default(size=40))
    image = Image.new('L', (1400, 140), 255)
    ImageDraw.Draw(image).text((30, 40), text, fill=0, font=font)
    return image


def sample_pdf(text=SAMPLE):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont('Helvetica', 14)
    pdf.drawString(72, 760, 'TRANSCRIPT OF RECORDS (sample)')
    pdf.drawString(72, 730, text)
    pdf.save()
    return buffer.getvalue()


def has_sample(found):
    words = ['bachelor', 'education', 'mathematics']
    missing = [w for w in words if w not in found.lower()]
    if missing:
        raise AssertionError(f'missing {missing} in {found.strip()[:80]!r}')


@check('Python version')
def python_version():
    if sys.version_info < (3, 11):
        raise AssertionError(f'Python {platform.python_version()}; use 3.12 (Ubuntu 24.04)')
    return platform.python_version()


@check('Tesseract OCR on an image')
def ocr_image():
    import pytesseract
    version = pytesseract.get_tesseract_version()
    has_sample(pytesseract.image_to_string(sample_image()))
    return f'Tesseract {version}'


@check('PDF text extraction with page positions')
def pdf_text():
    import pdfplumber
    with pdfplumber.open(io.BytesIO(sample_pdf())) as pdf:
        page = pdf.pages[0]
        has_sample(page.extract_text() or '')
        words = page.extract_words()
    return f'{len(words)} words with coordinates'


@check('Scanned PDF: render the page, then OCR')
def pdf_ocr():
    import pypdfium2
    import pytesseract
    document = pypdfium2.PdfDocument(sample_pdf())
    image = document[0].render(scale=300 / 72).to_pil()
    has_sample(pytesseract.image_to_string(image))
    return f'page rendered at 300 dpi ({image.width}x{image.height})'


@check('spaCy English model')
def spacy_model():
    import spacy
    name = os.environ.get('SPACY_MODEL', 'en_core_web_sm')
    nlp = spacy.load(name)
    doc = nlp('Juan Dela Cruz finished a Master of Arts in Education at '
              'Eastern Visayas State University in 2019.')
    entities = ', '.join(f'{e.text} [{e.label_}]' for e in doc.ents)
    return f'{name} {nlp.meta.get("version", "")}; entities: {entities or "none"}'


@check('SBERT sentence similarity')
def sbert():
    from sentence_transformers import SentenceTransformer, util
    name = os.environ.get('SBERT_MODEL', 'sentence-transformers/all-MiniLM-L6-v2')
    model = SentenceTransformer(name)
    competency = 'Designs lesson plans and teaches mathematics to college students'
    related = 'Taught algebra and statistics courses and prepared course syllabi'
    unrelated = 'Repaired diesel engines and maintained heavy equipment'
    vectors = model.encode([competency, related, unrelated], convert_to_tensor=True)
    near = float(util.cos_sim(vectors[0], vectors[1]))
    far = float(util.cos_sim(vectors[0], vectors[2]))
    if not near > far:
        raise AssertionError(f'related {near:.2f} is not above unrelated {far:.2f}')
    return f'{name} on {model.device}; related {near:.2f} > unrelated {far:.2f}'


@check('Models load without internet (offline mode)')
def offline():
    if os.environ.get('HF_HUB_OFFLINE') != '1':
        raise AssertionError('HF_HUB_OFFLINE is not 1; set it in .env after downloading '
                             'the model (step 5.6)')
    return f"models in {os.environ.get('HF_HOME', '~/.cache/huggingface')}"


@check('Memory used by this check')
def memory():
    try:
        import resource
    except ImportError:
        return 'not measured on this system'
    peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    return f'peak {peak_mb:.0f} MB (OCR, spaCy, and SBERT loaded together)'


if __name__ == '__main__':
    load_env()
    print(f'AI runtime check - {platform.node()} - {platform.platform()}')
    for step in (python_version, ocr_image, pdf_text, pdf_ocr, spacy_model, sbert, offline,
                 memory):
        step()
    passed = sum(RESULTS)
    print(f'\n{passed} of {len(RESULTS)} checks passed.')
    sys.exit(0 if all(RESULTS) else 1)
