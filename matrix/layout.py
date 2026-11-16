"""The official PART-TIME roster form (MATRIX_TEACHING APPLICANTS FOR PSB.xlsx).

Cell positions of the supplied worksheet: institutional heading A2:A4, roster
title A6, grouped column headers rows 8-9, department label row 10, sample
applicant rows from 11, signatories below the rows, and an unrelated stray
formula in T29 that is never copied into generated rosters.
"""

import re

SHEET = 'PART-TIME'
LAYOUT = 'part_time_v1'

HEADER_ROW = 8
SUBHEADER_ROW = 9
DEPARTMENT_ROW = 10
FIRST_DATA_ROW = 11
LAST_COLUMN = 'T'

COLUMNS = tuple('ABCDEFGHIJKLMNOPQRST')

# Default labels, used only when the template cell is empty.
DEFAULT_LABELS = {
    'A': 'NO.', 'B': 'LAST NAME', 'C': 'FIRST NAME', 'D': 'MIDDLE NAME', 'E': 'CONTACT NO.',
    'F': 'EMAIL ADDRESS', 'G': 'BIRTHDATE', 'H': 'SEX', 'I': 'ADDRESS', 'J': 'CIVIL STATUS',
    'K': 'PROFESSION LICENSED', 'L': "BACHELOR'S", 'M': 'MASTERAL', 'N': 'DOCTORAL',
    'O': 'BACCALAUREATE', 'P': 'MASTERAL', 'Q': 'DOCTORAL', 'R': 'RELEVANT WORK EXPERIENCE',
    'S': 'RELEVANT SEMINAR/TRAINING', 'T': 'REMARKS',
}
GROUPS = (('L', 'N', 'ACADEMIC BACKGROUND'), ('O', 'Q', 'SCHOOL GRADUATED'))
GROUPED = set('LMNOPQ')

# Header cells that identify the form; checked when a template is registered.
REQUIRED_HEADERS = {
    'B8': 'LAST NAME', 'C8': 'FIRST NAME', 'D8': 'MIDDLE NAME', 'F8': 'EMAIL ADDRESS',
    'L8': 'ACADEMIC BACKGROUND', 'O8': 'SCHOOL GRADUATED', 'R8': 'RELEVANT WORK EXPERIENCE',
    'T8': 'REMARKS',
}

# openpyxl paperSize codes -> (name, width_in, height_in) in portrait.
PAPER_SIZES = {1: ('Letter', 8.5, 11), 5: ('Legal', 8.5, 14), 9: ('A4', 8.27, 11.69),
               14: ('Folio (8.5 x 13 in)', 8.5, 13)}
DEFAULT_PAPER = 14


def norm(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip().upper()


def text(value):
    return re.sub(r'\s+', ' ', str(value or '')).strip()


def validate(workbook):
    """Raise ValueError unless the workbook has the expected PART-TIME layout."""
    if SHEET not in workbook.sheetnames:
        raise ValueError(f'The workbook has no "{SHEET}" worksheet.')
    ws = workbook[SHEET]
    wrong = [f'{cell} (expected "{label}")' for cell, label in REQUIRED_HEADERS.items()
             if norm(ws[cell].value) != label]
    if wrong:
        raise ValueError('The worksheet does not match the official PART-TIME roster layout: '
                         + ', '.join(wrong))


def read_header(workbook):
    """Header wording, column labels/widths, and paper size from the template."""
    ws = workbook[SHEET]
    labels = {}
    for col in COLUMNS:
        cell = ws[f'{col}{SUBHEADER_ROW}'] if col in GROUPED else ws[f'{col}{HEADER_ROW}']
        labels[col] = text(cell.value) or DEFAULT_LABELS[col]
    widths = {col: round(ws.column_dimensions[col].width or 10, 2) for col in COLUMNS}
    paper = ws.page_setup.paperSize or DEFAULT_PAPER
    paper = int(paper) if str(paper).isdigit() and int(paper) in PAPER_SIZES else DEFAULT_PAPER
    return {
        'institution': [text(ws[f'A{r}'].value) for r in (2, 3, 4) if text(ws[f'A{r}'].value)],
        'title': text(ws['A6'].value),
        'column_labels': labels,
        'groups': [{'first': a, 'last': b, 'label': text(ws[f'{a}{HEADER_ROW}'].value) or label}
                   for a, b, label in GROUPS],
        'column_widths': widths,
        'paper_size': paper,
        'paper_name': PAPER_SIZES[paper][0],
    }


def build_sample_template(path):
    """Write a synthetic workbook with the official layout (placeholder heading
    and signatories). Used by tests and on machines without the official file;
    it is clearly labeled and never a substitute for the HR-supplied form."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, Side

    wb = Workbook()
    ws = wb.active
    ws.title = SHEET
    bold = Font(name='Calibri', size=12, bold=True)
    normal = Font(name='Calibri', size=12)
    center = Alignment(horizontal='center', vertical='center', wrap_text=True)
    thin = Side(style='thin')
    box = Border(left=thin, right=thin, top=thin, bottom=thin)
    for row, value, font in ((2, 'Republic of the Philippines', normal),
                             (3, 'SAMPLE INSTITUTION (development template)', bold),
                             (4, 'Sample address', normal),
                             (6, 'ROSTER OF APPLICANTS FOR PART-TIME INSTRUCTOR APPLICANTS', bold)):
        ws.merge_cells(f'A{row}:T{row}')
        ws[f'A{row}'] = value
        ws[f'A{row}'].font = font
        ws[f'A{row}'].alignment = Alignment(horizontal='center')
    for col in COLUMNS:
        if col in GROUPED:
            ws[f'{col}{SUBHEADER_ROW}'] = DEFAULT_LABELS[col]
        else:
            ws.merge_cells(f'{col}{HEADER_ROW}:{col}{SUBHEADER_ROW}')
            ws[f'{col}{HEADER_ROW}'] = DEFAULT_LABELS[col]
    for first, last, label in GROUPS:
        ws.merge_cells(f'{first}{HEADER_ROW}:{last}{HEADER_ROW}')
        ws[f'{first}{HEADER_ROW}'] = label
    ws.merge_cells(f'A{DEPARTMENT_ROW}:T{DEPARTMENT_ROW}')
    ws[f'A{DEPARTMENT_ROW}'] = 'DEPARTMENT'
    for row in range(HEADER_ROW, FIRST_DATA_ROW + 6):
        for col in COLUMNS:
            cell = ws[f'{col}{row}']
            cell.border = box
            cell.alignment = center
            cell.font = bold if row < FIRST_DATA_ROW else normal
    for i in range(6):
        ws[f'A{FIRST_DATA_ROW + i}'] = str(i + 1)
    widths = (6, 20, 17, 15, 15, 19, 14, 11, 17, 11, 19, 20, 22, 15, 21, 21, 14, 23, 24, 18)
    for col, width in zip(COLUMNS, widths):
        ws.column_dimensions[col].width = width
    left = Alignment(horizontal='left', wrap_text=True)
    for cell, value, font in (('A20', 'Prepared by:', normal), ('G20', 'Noted by:', normal),
                              ('A22', 'SIGNATORY NAME', bold), ('G22', 'SIGNATORY NAME', bold),
                              ('A23', 'Position', normal), ('G23', 'Position', normal)):
        ws[cell] = value
        ws[cell].font = font
        ws[cell].alignment = left
    for rng in ('A20:B20', 'G20:H20', 'A22:C22', 'G22:I22', 'A23:C23', 'G23:I23'):
        ws.merge_cells(rng)
    ws['T29'] = '=38+16'  # mirrors the stray formula in the official file
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = DEFAULT_PAPER
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    wb.save(path)
    return path
