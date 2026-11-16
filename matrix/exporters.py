"""Excel and PDF exports of one roster version (FR-023).

Both exporters consume the same roster data (stored row text, header and
signatory snapshots), so the two files always show the same content:

- Excel fills a copy of the approved template workbook: the heading, logos,
  grouped headers, and styles are the official file's own. Sample rows, old
  signatories, and the stray T29 formula are removed; entries and signatories
  are written fresh, and every applicant value is stored as text.
- PDF is drawn with ReportLab on the template's paper size in landscape, with
  header rows repeated on every page and the signatories after the last row.
"""

import io
import math
from copy import copy
from xml.sax.saxutils import escape

from . import layout
from .builder import LEGEND, UNVERIFIED_MARK

FORMULA_PREFIXES = ('=', '+', '-', '@', '\t', '\r')
LEFT_ALIGNED = set('IKLMNOPQRST')
SIGNATORY_CELLS = ('A20', 'G20', 'A22', 'G22', 'A23', 'G23')


def roster_data(roster, entries=None):
    """Plain data shared by both exporters."""
    entries = list(entries if entries is not None else roster.entries.order_by('sequence_number'))
    return {
        'header': roster.header_snapshot,
        'signatories': roster.signatory_snapshot,
        'rows': [entry.exported_values for entry in entries],
        'draft': roster.status != 'approved',
        'version': roster.version,
        'reference': roster.vacancy.reference,
        'cutoff': roster.header_snapshot.get('cutoff_display', ''),
    }


def _has_marks(rows):
    return any(UNVERIFIED_MARK in (row.get(c) or '') or '(not provided)' in (row.get(c) or '')
               for row in rows for c in layout.COLUMNS)


def _empty_text(data):
    return f'No submitted applications for this vacancy as of the cutoff ({data["cutoff"]}).'


def _footer_text(data):
    return f'{data["reference"]} - Roster version {data["version"]}' + \
        (' - DRAFT' if data['draft'] else '')


# --------------------------------------------------------------------------
# Excel
# --------------------------------------------------------------------------

def put_text(cell, value):
    """Write applicant/HR text so it can never become a spreadsheet formula."""
    cell.value = value if value not in (None, '') else None
    if isinstance(cell.value, str):
        cell.data_type = 's'
        if cell.value.startswith(FORMULA_PREFIXES):
            cell.quotePrefix = True


def _row_height(values, widths, font_size):
    lines = 1
    for col, value in values.items():
        if not value:
            continue
        per_line = max(4, int((widths.get(col) or 10) * 0.85 * 12 / max(font_size, 8)))
        lines = max(lines, sum(max(1, math.ceil(len(part) / per_line))
                               for part in str(value).split('\n')))
    return min(409, max(15.75, round(lines * font_size * 1.3 + 4, 2)))


def build_xlsx(template_path, data):
    from openpyxl import load_workbook
    from openpyxl.formatting.formatting import ConditionalFormattingList
    from openpyxl.styles import Alignment, Font

    wb = load_workbook(template_path)
    for name in list(wb.sheetnames):
        if name != layout.SHEET:
            del wb[name]
    ws = wb[layout.SHEET]
    first = layout.FIRST_DATA_ROW
    entry_styles = {col: copy(ws[f'{col}{first}']._style) for col in layout.COLUMNS}
    sig_styles = {ref: copy(ws[ref]._style) for ref in SIGNATORY_CELLS}
    font_size = ws[f'B{first}'].font.sz or 12
    widths = {col: ws.column_dimensions[col].width or 10 for col in layout.COLUMNS}

    put_text(ws[f'A{layout.DEPARTMENT_ROW}'], data['header'].get('department', ''))

    # Remove sample rows, old signatories, and anything else below the headers
    # (including the unrelated T29 formula).
    for rng in list(ws.merged_cells.ranges):
        if rng.max_row >= first:
            ws.unmerge_cells(str(rng))
    if ws.max_row >= first:
        ws.delete_rows(first, ws.max_row - first + 1)
    for index in [r for r in ws.row_dimensions if r >= first]:
        del ws.row_dimensions[index]
    ws.conditional_formatting = ConditionalFormattingList()

    if data['draft']:
        ws.merge_cells('A7:T7')
        put_text(ws['A7'], 'DRAFT - NOT APPROVED FOR OFFICIAL USE')
        ws['A7'].font = Font(bold=True, color='C00000', size=12)
        ws['A7'].alignment = Alignment(horizontal='center')

    row = first
    if not data['rows']:
        for col in layout.COLUMNS:
            ws[f'{col}{row}']._style = copy(entry_styles[col])
        ws.merge_cells(f'A{row}:{layout.LAST_COLUMN}{row}')
        put_text(ws[f'A{row}'], _empty_text(data))
        ws.row_dimensions[row].height = 24
        row += 1
    for values in data['rows']:
        for col in layout.COLUMNS:
            cell = ws[f'{col}{row}']
            cell._style = copy(entry_styles[col])
            put_text(cell, values.get(col, ''))
            cell.alignment = Alignment(
                horizontal='left' if col in LEFT_ALIGNED else 'center',
                vertical='top', wrap_text=True)
        ws.row_dimensions[row].height = _row_height(values, widths, font_size)
        row += 1
    last_row = row - 1

    if _has_marks(data['rows']):
        ws.merge_cells(f'A{row}:{layout.LAST_COLUMN}{row}')
        put_text(ws[f'A{row}'], LEGEND)
        ws[f'A{row}'].font = Font(italic=True, size=10)
        ws[f'A{row}'].alignment = Alignment(wrap_text=True, vertical='top')
        ws.row_dimensions[row].height = 30
        row += 1

    sig = row + 2
    signatories = data['signatories']
    blocks = (('A', 'B', 'C', 'Prepared by:', signatories.get('prepared_by') or {}),
              ('G', 'H', 'I', 'Noted by:', signatories.get('noted_by') or {}))
    for start, label_end, name_end, label, person in blocks:
        for offset, text, src, end in ((0, label, f'{start}20', label_end),
                                       (2, (person.get('name') or '').upper(), f'{start}22',
                                        name_end),
                                       (3, person.get('position') or '', f'{start}23', name_end)):
            cell = ws[f'{start}{sig + offset}']
            cell._style = copy(sig_styles[src])
            put_text(cell, text)
            ws.merge_cells(f'{start}{sig + offset}:{end}{sig + offset}')
    ws.row_dimensions[sig].height = 30
    end_row = sig + 3

    # Defensive: no formulas of any kind in a generated roster.
    for cells in ws.iter_rows():
        for cell in cells:
            if cell.data_type == 'f':
                cell.value = None

    setup = ws.page_setup
    setup.orientation = 'landscape'
    if not setup.paperSize:
        setup.paperSize = layout.DEFAULT_PAPER
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    setup.fitToWidth = 1
    setup.fitToHeight = 0
    ws.print_title_rows = f'{layout.HEADER_ROW}:{layout.DEPARTMENT_ROW}'
    ws.print_area = f'A1:{layout.LAST_COLUMN}{end_row}'
    ws.oddFooter.left.text = _footer_text(data)
    ws.oddFooter.right.text = 'Page &P of &N'
    if data['draft']:
        ws.oddHeader.center.text = 'DRAFT - NOT APPROVED FOR OFFICIAL USE'
    wb.properties.title = f'Roster {data["reference"]} v{data["version"]}'
    wb.properties.creator = 'EVSU Recruitment'

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), {'last_entry_row': last_row, 'signatory_row': sig}


# --------------------------------------------------------------------------
# PDF
# --------------------------------------------------------------------------

def _logos(template_path):
    from openpyxl import load_workbook
    try:
        ws = load_workbook(template_path)[layout.SHEET]
        images = sorted(ws._images, key=lambda img: img.anchor._from.col)
        return [img._data() for img in images[:2]]
    except Exception:  # logos are decorative; never block an export
        return []


def _image(data, max_height):
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image
    width, height = ImageReader(io.BytesIO(data)).getSize()
    scale = max_height / float(height)
    return Image(io.BytesIO(data), width=width * scale, height=max_height)


# Relative PDF column widths, tuned so short values (an 11-digit contact
# number, header words such as BACCALAUREATE) do not split and the long
# experience/training descriptions get the most room. The Excel export keeps
# the template's own column widths.
PDF_WEIGHTS = {'A': 2.6, 'B': 6.2, 'C': 5.8, 'D': 5.8, 'E': 8.4, 'F': 8.4, 'G': 7.4, 'H': 4.0,
               'I': 7.6, 'J': 5.4, 'K': 7.8, 'L': 8.2, 'M': 7.0, 'N': 7.0, 'O': 9.0, 'P': 7.0,
               'Q': 7.0, 'R': 11.8, 'S': 10.8, 'T': 6.6}


def _pdf_widths(widths, usable):
    total = sum(PDF_WEIGHTS.values())
    return [usable * PDF_WEIGHTS[c] / total for c in layout.COLUMNS]


def _para(text, style):
    from reportlab.platypus import Paragraph
    return Paragraph(escape(text or '').replace('\n', '<br/>'), style)


def build_pdf(template_path, data):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER
    from reportlab.lib.pagesizes import landscape
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas as pdf_canvas
    from reportlab.platypus import KeepTogether, SimpleDocTemplate, Spacer, Table, TableStyle

    header = data['header']
    _, paper_w, paper_h = layout.PAPER_SIZES.get(header.get('paper_size'),
                                                 layout.PAPER_SIZES[layout.DEFAULT_PAPER])
    pagesize = landscape((paper_w * inch, paper_h * inch))
    margin = 0.35 * inch
    footer = _footer_text(data)
    draft = data['draft']

    class NumberedCanvas(pdf_canvas.Canvas):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self._pages = []

        def showPage(self):
            self._pages.append(dict(self.__dict__))
            self._startPage()

        def save(self):
            total = len(self._pages)
            for state in self._pages:
                self.__dict__.update(state)
                self._decorate(total)
                super().showPage()
            super().save()

        def _decorate(self, total):
            width, _ = pagesize
            self.setFont('Helvetica', 7)
            self.setFillColor(colors.black)
            self.drawString(margin, 0.25 * inch, footer)
            self.drawRightString(width - margin, 0.25 * inch,
                                 f'Page {self._pageNumber} of {total}')
            if draft:
                self.saveState()
                self.setFont('Helvetica-Bold', 72)
                self.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.12))
                self.translate(width / 2, pagesize[1] / 2)
                self.rotate(25)
                self.drawCentredString(0, 0, 'DRAFT')
                self.restoreState()

    base = ParagraphStyle('cell', fontName='Helvetica', fontSize=6.8, leading=8.2)
    head = ParagraphStyle('head', parent=base, fontName='Helvetica-Bold', alignment=TA_CENTER,
                          fontSize=5.8, leading=7)
    center = ParagraphStyle('center', parent=base, alignment=TA_CENTER)
    title_style = ParagraphStyle('title', fontName='Helvetica-Bold', fontSize=11,
                                 alignment=TA_CENTER, leading=14)
    inst = ParagraphStyle('inst', fontName='Helvetica', fontSize=10, alignment=TA_CENTER,
                          leading=12.5)
    inst_bold = ParagraphStyle('instb', parent=inst, fontName='Helvetica-Bold')
    sig_style = ParagraphStyle('sig', fontName='Helvetica', fontSize=9, leading=11)
    sig_name = ParagraphStyle('signame', parent=sig_style, fontName='Helvetica-Bold')

    story = []
    lines = header.get('institution', [])
    heading = [_para(line, inst_bold if i == 1 else inst) for i, line in enumerate(lines)]
    logos = [_image(d, 0.7 * inch) for d in _logos(template_path)]
    usable = pagesize[0] - 2 * margin
    if len(logos) == 2:
        story.append(Table([[logos[0], heading, logos[1]]],
                           colWidths=[1.2 * inch, usable * 0.45, 1.2 * inch],
                           style=[('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
                                  ('ALIGN', (0, 0), (0, 0), 'RIGHT'),
                                  ('ALIGN', (2, 0), (2, 0), 'LEFT')]))
    else:
        story.extend(heading)
    story.append(Spacer(1, 6))
    story.append(_para(header.get('title', ''), title_style))
    if draft:
        story.append(_para('DRAFT - NOT APPROVED FOR OFFICIAL USE',
                           ParagraphStyle('draft', parent=title_style, textColor=colors.red,
                                          fontSize=9)))
    story.append(Spacer(1, 6))

    cols = layout.COLUMNS
    widths = header.get('column_widths') or {c: 15 for c in cols}
    col_widths = _pdf_widths(widths, usable)
    labels = header.get('column_labels', layout.DEFAULT_LABELS)
    groups = {g['first']: g for g in header.get('groups', [])}
    idx = {c: i for i, c in enumerate(cols)}

    row0, row1 = [], []
    for col in cols:
        if col in layout.GROUPED:
            row0.append(_para(groups[col]['label'], head) if col in groups else '')
            row1.append(_para(labels[col], head))
        else:
            row0.append(_para(labels[col], head))
            row1.append('')
    rows = [row0, row1, [_para(header.get('department', ''), head)] + [''] * (len(cols) - 1)]
    styles = [
        ('GRID', (0, 0), (-1, -1), 0.5, colors.black),
        ('VALIGN', (0, 0), (-1, 2), 'MIDDLE'),
        ('VALIGN', (0, 3), (-1, -1), 'TOP'),
        ('BACKGROUND', (0, 0), (-1, 2), colors.Color(0.93, 0.93, 0.93)),
        ('SPAN', (0, 2), (-1, 2)),
        ('LEFTPADDING', (0, 0), (-1, -1), 2), ('RIGHTPADDING', (0, 0), (-1, -1), 2),
        ('TOPPADDING', (0, 0), (-1, -1), 2), ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]
    for col in cols:
        if col not in layout.GROUPED:
            styles.append(('SPAN', (idx[col], 0), (idx[col], 1)))
    for g in groups.values():
        styles.append(('SPAN', (idx[g['first']], 0), (idx[g['last']], 0)))

    if not data['rows']:
        rows.append([_para(_empty_text(data), center)] + [''] * (len(cols) - 1))
        styles.append(('SPAN', (0, 3), (-1, 3)))
    for values in data['rows']:
        rows.append([_para(values.get(c, ''), base if c in LEFT_ALIGNED else center)
                     for c in cols])
    table = Table(rows, colWidths=col_widths, repeatRows=3, splitByRow=1)
    table.setStyle(TableStyle(styles))
    story.append(table)

    if _has_marks(data['rows']):
        story.append(Spacer(1, 4))
        story.append(_para(LEGEND, ParagraphStyle('legend', parent=base, fontName='Helvetica-Oblique')))

    signatories = data['signatories']
    blocks = []
    for label, key in (('Prepared by:', 'prepared_by'), ('Noted by:', 'noted_by')):
        person = signatories.get(key) or {}
        blocks.append([_para(label, sig_style), Spacer(1, 18),
                       _para((person.get('name') or '').upper(), sig_name),
                       _para(person.get('position') or '', sig_style)])
    sig_table = Table([blocks], colWidths=[usable * 0.3, usable * 0.3],
                      hAlign='LEFT', style=[('VALIGN', (0, 0), (-1, -1), 'TOP')])
    story.append(Spacer(1, 18))
    story.append(KeepTogether([sig_table]))

    out = io.BytesIO()
    doc = SimpleDocTemplate(out, pagesize=pagesize, leftMargin=margin, rightMargin=margin,
                            topMargin=0.4 * inch, bottomMargin=0.5 * inch,
                            title=f'Roster {data["reference"]} v{data["version"]}',
                            author='EVSU Recruitment')
    doc.build(story, canvasmaker=NumberedCanvas)
    return out.getvalue()
