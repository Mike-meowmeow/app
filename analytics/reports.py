"""Report contents and Excel/PDF rendering (FR-016).

Each report has a header that states its scope, generation time, generator,
and definition version. Text cells are written as text (formula-like values
get Excel's quote prefix), so recorded values can never run as formulas.
Reports contain recorded facts and counts, not evaluation narratives.
"""

import io

from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from committee.models import EvaluationStatus, InterviewEvaluation
from recruitment.models import ApplicationFact, FactReviewStatus, FactType, RecruitmentDecision

from . import metrics
from .models import ReportFormat, ReportType


def _date(value, fmt='%b %d, %Y'):
    return timezone.localtime(value).strftime(fmt) if value else ''


def _name(user):
    return (user.full_name or user.username) if user else ''


# --------------------------------------------------------------------------
# Contents: (title, columns, rows, notes)
# --------------------------------------------------------------------------

def _applicants(vacancies, start, end):
    apps = list(metrics.scoped_applications(vacancies, start, end).select_related(
        'vacancy__department', 'applicant__user').order_by(
        'vacancy__position_title', 'applicant__user__last_name', 'applicant__user__first_name'))
    ids = [a.pk for a in apps]
    outcomes = {}
    for fact in ApplicationFact.objects.filter(
            application_id__in=ids, fact_type=FactType.QUALIFICATION_ASSESSMENT) \
            .exclude(review_status=FactReviewStatus.SUPERSEDED):
        outcomes.setdefault(fact.application_id, []).append(fact.value.get('outcome', ''))
    evaluations = {}
    for e in InterviewEvaluation.objects.filter(application_id__in=ids,
                                                status=EvaluationStatus.SUBMITTED) \
            .select_related('superseded_by'):
        if e.is_current:
            evaluations[e.application_id] = evaluations.get(e.application_id, 0) + 1
    latest = {}
    for d in RecruitmentDecision.objects.filter(application_id__in=ids,
                                                superseded_by__isnull=True).order_by('decision_at'):
        latest[d.application_id] = d
    rows = []
    for a in apps:
        user = a.applicant.user
        found = outcomes.get(a.pk, [])
        summary = ', '.join(f'{label}: {found.count(key)}' for key, label in (
            ('meets', 'Meets'), ('does_not_meet', 'Does not meet'),
            ('needs_review', 'Needs review'), ('not_required', 'Not required'))
            if found.count(key)) or 'Not assessed'
        decision = latest.get(a.pk)
        rows.append([a.reference, user.last_name, user.first_name, a.vacancy.position_title,
                     a.vacancy.department.code, _date(a.submitted_at), a.get_status_display(),
                     summary, evaluations.get(a.pk, 0),
                     f'{decision.get_decision_type_display()}: {decision.get_outcome_display()}'
                     if decision else ''])
    columns = ['Reference', 'Last name', 'First name', 'Vacancy', 'Department', 'Submitted',
               'Status (internal)', 'Qualification review', 'Evaluations',
               'Latest decision']
    return 'Applicant summary', columns, rows, [
        'Status is the internal workflow status, including decisions not yet released to '
        'applicants. Evaluations counts current submitted evaluations.']


def _vacancies(vacancies, start, end):
    fills = metrics.time_to_fill(vacancies)
    rows = []
    for v in vacancies.select_related('department').order_by('position_title'):
        m = metrics.compute(vacancies.filter(pk=v.pk), period_start=start, period_end=end)
        rows.append([v.reference, v.position_title, v.department.code, v.get_status_display(),
                     v.positions_available, _date(v.posted_at), _date(v.deadline_at),
                     m['submitted_applications'], m['unique_applicants'], m['shortlisted_count'],
                     m['completed_interviews'], m['recommended_count'], m['hired_count'],
                     metrics.format_duration(fills.get(v.pk))])
    columns = ['Reference', 'Position', 'Department', 'Status', 'Positions', 'Published',
               'Deadline', 'Submitted', 'Unique applicants', 'Ever shortlisted',
               'Interviews completed', 'Recommended', 'Hired', 'Time to fill']
    return 'Vacancy summary', columns, rows, []


def _decisions(vacancies, start, end):
    apps = metrics.scoped_applications(vacancies, start, end)
    rows = []
    for d in RecruitmentDecision.objects.filter(application__in=apps).select_related(
            'application__vacancy', 'application__applicant__user', 'decided_by',
            'released_by', 'superseded_by').order_by('application__vacancy__position_title',
                                                     'decision_at'):
        user = d.application.applicant.user
        rows.append([d.application.reference, f'{user.last_name}, {user.first_name}',
                     d.application.vacancy.position_title, d.get_decision_type_display(),
                     d.get_outcome_display(), _date(d.decision_at), _name(d.decided_by),
                     'Current' if d.is_current else 'Superseded',
                     _date(d.released_at) if d.released_at else 'Not released',
                     d.appointment_reference])
    columns = ['Application', 'Applicant', 'Vacancy', 'Decision', 'Outcome', 'Decided',
               'Recorded by', 'Record', 'Released', 'Appointment ref.']
    return 'Recorded decisions', columns, rows, [
        'Superseded records are corrections kept for history. Justifications are not '
        'reproduced; see the application record.']


def _statistics(vacancies, start, end):
    m = metrics.compute(vacancies, period_start=start, period_end=end)
    r = metrics.format_rate
    rows = [
        ['Submitted applications', m['submitted_applications']],
        ['Unique applicants', m['unique_applicants']],
        ['Applications ever shortlisted', m['shortlisted_count']],
        ['Shortlisting rate', r(m['shortlisting_rate'])],
        ['Interviews scheduled (excluding canceled)', m['scheduled_interviews']],
        ['Interviews completed (applicant attended)', m['completed_interviews']],
        ['No-shows', m['no_show_interviews']],
        ['Canceled interviews', m['canceled_interviews']],
        ['Interview completion rate', r(m['interview_completion_rate'])],
        ['Applications with completed committee review', m['reviewed_applications']],
        ['Recommended', m['recommended_count']],
        ['Recommendation rate', r(m['recommendation_rate'])],
        ['Current submitted evaluations', m['current_evaluations']],
        ['Hired', m['hired_count']],
        ['Withdrawn', m['withdrawn_count']],
        ['Active vacancies (accepting applications now)', m['active_vacancies']],
        ['Filled vacancies', m['filled_vacancies']],
        ['Time to fill (mean of filled vacancies)', metrics.format_duration(
            m['time_to_fill_seconds'])],
    ]
    rows += [[f'Current status: {label}', n] for label, n in m['status_distribution']]
    rows += [[f'Mean days {s["label"]} ({s["applications"]} applications)',
              'Not applicable' if s['mean_days'] is None else s['mean_days']]
             for s in m['stage_durations']]
    rows += [[f'Submissions {month}', n] for month, n in m['trend']]
    notes = [f'{name}: {text}' for name, text in metrics.DEFINITIONS]
    return 'Recruitment statistics', ['Measure', 'Value'], rows, notes


BUILDERS = {ReportType.APPLICANTS: _applicants, ReportType.VACANCIES: _vacancies,
            ReportType.DECISIONS: _decisions, ReportType.STATISTICS: _statistics}


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------

FORMULA_START = ('=', '+', '-', '@')


def _xlsx(title, columns, rows, notes, meta):
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    header_lines = [title, 'EVSU-Ormoc HRMDO - Recruitment report', f"Scope: {meta['scope']}",
                    f"Generated {meta['generated_at']} by {meta['generated_by']} "
                    f"(definitions {meta['definition_version']})"]
    for i, line in enumerate(header_lines, start=1):
        cell = ws.cell(row=i, column=1)
        cell.value = line
        cell.data_type = 's'
        cell.font = Font(bold=(i == 1), size=14 if i == 1 else 10)
    head = len(header_lines) + 2
    fill = PatternFill('solid', fgColor='800000')
    for c, name in enumerate(columns, start=1):
        cell = ws.cell(row=head, column=c, value=name)
        cell.font = Font(bold=True, color='FFFFFF')
        cell.fill = fill
        cell.alignment = Alignment(wrap_text=True, vertical='top')
    for r, row in enumerate(rows, start=head + 1):
        for c, value in enumerate(row, start=1):
            cell = ws.cell(row=r, column=c)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                cell.value = value
            else:
                text = '' if value is None else str(value)
                cell.value = text
                cell.data_type = 's'
                if text.startswith(FORMULA_START):
                    cell.quotePrefix = True
            cell.alignment = Alignment(wrap_text=True, vertical='top')
    if not rows:
        ws.cell(row=head + 1, column=1, value='No records in this scope.')
    for c, name in enumerate(columns, start=1):
        width = max([len(str(name))] + [len(str(row[c - 1])) for row in rows[:200]])
        ws.column_dimensions[get_column_letter(c)].width = min(max(width + 2, 10), 60)
    note_row = head + max(len(rows), 1) + 2
    for i, note in enumerate(notes):
        cell = ws.cell(row=note_row + i, column=1, value=note)
        cell.data_type = 's'
        cell.font = Font(italic=True, size=9)
    ws.freeze_panes = ws.cell(row=head + 1, column=1)
    ws.print_title_rows = f'{head}:{head}'
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _pdf(title, columns, rows, notes, meta):
    styles = getSampleStyleSheet()
    small = styles['BodyText'].clone('small', fontSize=7, leading=8.5)
    head = small.clone('head', textColor=colors.white, fontName='Helvetica-Bold')
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4), leftMargin=10 * mm,
                            rightMargin=10 * mm, topMargin=10 * mm, bottomMargin=12 * mm,
                            title=title)

    def esc(value):
        return (str(value) if value is not None else '').replace('&', '&amp;') \
            .replace('<', '&lt;').replace('>', '&gt;')

    story = [Paragraph(esc(title), styles['Title']),
             Paragraph('EVSU-Ormoc HRMDO - Recruitment report', styles['Normal']),
             Paragraph(f"Scope: {esc(meta['scope'])}", small),
             Paragraph(f"Generated {esc(meta['generated_at'])} by {esc(meta['generated_by'])} "
                       f"(definitions {esc(meta['definition_version'])})", small),
             Spacer(1, 4 * mm)]
    data = [[Paragraph(esc(c), head) for c in columns]]
    data += [[Paragraph(esc(v), small) for v in row] for row in rows]
    if not rows:
        data.append([Paragraph('No records in this scope.', small)] + [''] * (len(columns) - 1))
    width = doc.width / len(columns)
    table = Table(data, colWidths=[width] * len(columns), repeatRows=1)
    table.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#800000')),
        ('GRID', (0, 0), (-1, -1), 0.25, colors.grey),
        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
        ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor('#f4f4f4')]),
    ]))
    story += [table, Spacer(1, 4 * mm)]
    story += [Paragraph(esc(n), small) for n in notes]

    def footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont('Helvetica', 7)
        canvas.drawRightString(doc_.pagesize[0] - 10 * mm, 6 * mm, f'Page {doc_.page}')
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buffer.getvalue()


def build(report_type, fmt, vacancies, *, start=None, end=None, meta):
    title, columns, rows, notes = BUILDERS[report_type](vacancies, start, end)
    renderer = _xlsx if fmt == ReportFormat.XLSX else _pdf
    return renderer(title, columns, rows, notes, meta)
