"""Write made-up sample documents for testing the AI extraction.

    manage.py make_ai_samples <folder>

Every file is marked "SAMPLE - MADE-UP DATA - NOT A REAL RECORD" and uses
fictional people and institutions. Upload them as an applicant on a test
system to try the whole flow (verification -> extraction -> HR review). Two
files are images of text (a PNG, and a PDF with no digital text) to exercise OCR.
"""

import io
import textwrap
from pathlib import Path

from django.core.management.base import BaseCommand

MARK = 'SAMPLE - MADE-UP DATA - NOT A REAL RECORD'
NAME = 'MARIA SAMPLE SANTOS'

DOCUMENTS = {
    'TOR_transcript.pdf': [
        'LEYTE SAMPLE STATE UNIVERSITY',
        'College of Education',
        'OFFICIAL TRANSCRIPT OF RECORDS',
        f'Name: {NAME}',
        'Degree: Bachelor of Secondary Education',
        'Major in Mathematics',
        'Date of Graduation: March 25, 2015',
        'Subject Code   Descriptive Title   Final Grade   Units',
        'MATH 101   College Algebra   1.50   3',
        'MATH 205   Statistics and Probability   1.25   3',
        'EDUC 110   Principles of Teaching   1.75   3',
    ],
    'DIPLOMA_masters.pdf': [
        'SAMPLE INSTITUTE OF TECHNOLOGY',
        'confers upon',
        NAME,
        'the degree of',
        'Master of Arts in Education',
        'with all the rights and privileges thereto appertaining.',
        'Given this 30th day of April 2019 at Tacloban City.',
    ],
    'TRAINING_certificate.pdf': [
        'CERTIFICATE OF COMPLETION',
        'is awarded to',
        NAME,
        'for having successfully completed the "Outcomes-Based Teaching and Learning Workshop"',
        'held on March 3-5, 2019 at Ormoc City.',
        'This training has a total of 24 hours.',
        'Conducted by the Sample Teachers Training Center',
    ],
    'EMPLOYMENT_certificate.pdf': [
        'SAMPLE COLLEGE OF ORMOC',
        'CERTIFICATE OF EMPLOYMENT',
        'This is to certify that',
        f'{NAME} has been employed as Part-time Instructor at Sample College of Ormoc '
        'from June 1, 2016 to May 31, 2020.',
        'This certification is issued upon request for whatever purpose it may serve.',
    ],
    'PRC_license.pdf': [
        'Republic of the Philippines',
        'PROFESSIONAL REGULATION COMMISSION',
        'PROFESSIONAL IDENTIFICATION CARD',
        NAME,
        'Profession: Professional Teacher',
        'Registration No.: 1234567',
        'Registration Date: August 10, 2015',
        'Valid Until: September 1, 2027',
        'Passed the Licensure Examination for Professional Teachers with a rating of 85.40%',
        'given on March 29, 2015',
    ],
}
SCANNED = {
    'TRAINING_scanned.png': [
        'CERTIFICATE OF ATTENDANCE',
        'is given to',
        NAME,
        'for having attended the "Classroom Assessment Seminar"',
        'held on October 12, 2021',
        'with a total of 8 hours.',
        'Conducted by the Sample Division Office',
    ],
    'TOR_scanned.pdf': [
        'SAMPLE STATE COLLEGE',
        'TRANSCRIPT OF RECORDS',
        f'Name: {NAME}',
        'Degree: Bachelor of Science in Information Technology',
        'Date of Graduation: April 2, 2012',
    ],
}


def _pdf(lines):
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=A4)
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(50, 810, MARK)
    pdf.setFont('Helvetica', 11)
    y = 770
    for line in lines:
        for part in textwrap.wrap(line, 90) or ['']:
            pdf.drawString(50, y, part)
            y -= 22
    pdf.setFont('Helvetica-Bold', 9)
    pdf.drawString(50, 40, MARK)
    pdf.save()
    return buffer.getvalue()


def _image(lines):
    from PIL import Image, ImageDraw, ImageFont
    font = None
    for path in ('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf', 'C:/Windows/Fonts/arial.ttf'):
        if Path(path).exists():
            font = ImageFont.truetype(path, 34)
            break
    font = font or ImageFont.load_default()
    image = Image.new('L', (1700, 120 + 60 * (len(lines) + 2)), 255)
    draw = ImageDraw.Draw(image)
    draw.text((60, 40), MARK, fill=0, font=font)
    for i, line in enumerate(lines):
        draw.text((60, 140 + 60 * i), line, fill=0, font=font)
    return image


class Command(BaseCommand):
    help = 'Write made-up sample documents for AI extraction tests.'

    def add_arguments(self, parser):
        parser.add_argument('folder')

    def handle(self, folder, **options):
        out = Path(folder)
        out.mkdir(parents=True, exist_ok=True)
        for name, lines in DOCUMENTS.items():
            (out / name).write_bytes(_pdf(lines))
        for name, lines in SCANNED.items():
            image = _image(lines)
            if name.endswith('.png'):
                image.save(out / name)
            else:
                image.convert('RGB').save(out / name, 'PDF', resolution=200)
        self.stdout.write(f'Wrote {len(DOCUMENTS) + len(SCANNED)} made-up sample files to {out}.')
