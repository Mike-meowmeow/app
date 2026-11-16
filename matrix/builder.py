"""Roster row values (columns A:T) from preserved application information.

Values come from each application's current facts, which start from the
submitted snapshot and carry HR corrections. Every value is stored as the exact
text exported to Excel and PDF, so both files show the same content.

Display conventions (pending HR confirmation, FR-021 section 5):
- Order: last name, first name, middle name, then submission time.
  Row numbers are positions only, never ranks.
- A value HR has not accepted yet is marked with UNVERIFIED_MARK.
- A required value that is absent is shown as MISSING_TEXT.
- License, relevant experience, and relevant training with no items show
  NOT_APPLICABLE_TEXT. Master's and doctoral columns stay blank when not held.
- Several degrees are numbered so each degree lines up with its institution.
"""

from datetime import date
from decimal import Decimal, InvalidOperation

from applicants.models import CivilStatus, CompletionStatus, DegreeLevel, Sex
from recruitment.models import (ApplicationStatus, FactReviewStatus, FactType, RelevanceStatus)

UNVERIFIED_MARK = ' *'
MISSING_TEXT = '(not provided)'
NOT_APPLICABLE_TEXT = 'N/A'
LEGEND = ('* Submitted by the applicant and not yet verified by HR. '
          '"(not provided)" marks required information the applicant did not supply.')

MISSING = 'missing'
UNVERIFIED = 'unverified'
NOT_APPLICABLE = 'not_applicable'
FLAG_LABELS = {MISSING: 'Missing', UNVERIFIED: 'Not verified', NOT_APPLICABLE: 'Not applicable'}

IDENTITY_COLUMNS = (('B', 'last_name', True), ('C', 'first_name', True),
                    ('D', 'middle_name', False), ('E', 'contact_no', True), ('F', 'email', True),
                    ('G', 'birthdate', True), ('H', 'sex', True), ('I', 'address', True),
                    ('J', 'civil_status', True))
DEGREE_COLUMNS = ((DegreeLevel.BACHELOR, 'L', 'O', True), (DegreeLevel.MASTER, 'M', 'P', False),
                  (DegreeLevel.DOCTORATE, 'N', 'Q', False))
STATUS_REMARKS = {
    ApplicationStatus.WITHDRAWN: 'Withdrawn',
    ApplicationStatus.NOT_SELECTED: 'Not selected',
    ApplicationStatus.CLOSED_WITHOUT_SELECTION: 'Closed without selection',
}
MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August',
          'September', 'October', 'November', 'December')


def _date(value):
    if not value:
        return None
    try:
        return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def long_date(value):
    d = _date(value)
    return f'{MONTHS[d.month - 1]} {d.day}, {d.year}' if d else ''


def month_year(value):
    d = _date(value)
    return f'{MONTHS[d.month - 1][:3]} {d.year}' if d else ''


def _clean(value):
    return ' '.join(str(value).split()) if value not in (None, '') else ''


def _label(choices, value):
    return dict(choices).get(value, _clean(value))


def _numbered(lines):
    if len(lines) <= 1:
        return '\n'.join(lines)
    return '\n'.join(f'{i}. {line}' for i, line in enumerate(lines, 1))


class Row:
    def __init__(self):
        self.values = {}
        self.flags = {}

    def put(self, column, text, *, flag=None):
        if flag == MISSING:
            text = MISSING_TEXT
        elif flag == UNVERIFIED and text:
            text = text + UNVERIFIED_MARK
        self.values[column] = text
        if flag:
            self.flags[column] = flag


def _current(facts, fact_type):
    return [f for f in facts if f.fact_type == fact_type
            and f.review_status in (FactReviewStatus.ACCEPTED, FactReviewStatus.PENDING)]


def _pending(items):
    return any(f.review_status == FactReviewStatus.PENDING for f in items)


def _identity(row, facts):
    identity = next(iter(sorted(_current(facts, FactType.IDENTITY),
                                key=lambda f: f.created_at, reverse=True)), None)
    value = identity.value if identity else {}
    unverified = identity is None or identity.review_status != FactReviewStatus.ACCEPTED
    for column, field, required in IDENTITY_COLUMNS:
        raw = value.get(field)
        if field == 'birthdate':
            shown = long_date(raw)
        elif field == 'sex':
            shown = _label(Sex.choices, raw)
        elif field == 'civil_status':
            shown = _label(CivilStatus.choices, raw)
        else:
            shown = _clean(raw)
        if not shown:
            row.put(column, '', flag=MISSING if required else None)
        else:
            row.put(column, shown, flag=UNVERIFIED if unverified else None)


def _education(row, facts):
    items = _current(facts, FactType.EDUCATION)
    for level, degree_col, school_col, required in DEGREE_COLUMNS:
        chosen = sorted((f for f in items if f.value.get('degree_level') == level),
                        key=lambda f: (str(f.value.get('completed_on') or '9999'), f.created_at))
        if not chosen:
            row.put(degree_col, '', flag=MISSING if required else None)
            row.put(school_col, '', flag=MISSING if required else None)
            continue
        degrees, schools = [], []
        for fact in chosen:
            program = _clean(fact.value.get('program')) or MISSING_TEXT
            status = fact.value.get('completion_status')
            if status == CompletionStatus.ONGOING:
                program += ' (Ongoing)'
            elif status == CompletionStatus.NOT_COMPLETED:
                program += ' (Not completed)'
            degrees.append(program)
            schools.append(_clean(fact.value.get('institution')) or MISSING_TEXT)
        flag = UNVERIFIED if _pending(chosen) else None
        row.put(degree_col, _numbered(degrees), flag=flag)
        row.put(school_col, _numbered(schools), flag=flag)


def _period(value):
    start, end = month_year(value.get('start_date')), month_year(value.get('end_date'))
    if start and end:
        return f'{start} - {end}'
    if start:
        return f'{start} - Present'
    return end


def _relevant(row, column, facts, fact_type, describe):
    items = [f for f in _current(facts, fact_type)
             if f.relevance_status != RelevanceStatus.NOT_RELEVANT]
    if not items:
        row.put(column, NOT_APPLICABLE_TEXT, flag=NOT_APPLICABLE)
        return
    items.sort(key=lambda f: str(f.value.get('start_date') or ''), reverse=True)
    unverified = _pending(items) or any(f.relevance_status == RelevanceStatus.NOT_ASSESSED
                                        for f in items)
    row.put(column, '\n'.join(describe(f.value) for f in items),
            flag=UNVERIFIED if unverified else None)


def _experience_text(value):
    parts = ', '.join(p for p in (_clean(value.get('position')),
                                  _clean(value.get('organization'))) if p)
    period = _period(value)
    return f'{parts} ({period})' if period else parts


def _training_text(value):
    hours = value.get('hours')
    details = []
    if hours not in (None, ''):
        try:
            hours = f'{Decimal(str(hours)).normalize():f}'
        except InvalidOperation:
            hours = _clean(hours)
        details.append(f'{hours} hrs')
    if month_year(value.get('start_date')):
        details.append(month_year(value.get('start_date')))
    title = ', '.join(p for p in (_clean(value.get('title')), _clean(value.get('provider'))) if p)
    return f'{title} ({", ".join(details)})' if details else title


def _license(row, facts):
    items = _current(facts, FactType.LICENSE)
    if not items:
        row.put('K', NOT_APPLICABLE_TEXT, flag=NOT_APPLICABLE)
        return
    names = []
    for fact in items:
        name = _clean(fact.value.get('profession'))
        if name and name not in names:
            names.append(name)
    row.put('K', '\n'.join(names) or MISSING_TEXT, flag=UNVERIFIED if _pending(items) else None)


def document_flag(application):
    """Counts of current file versions that HR has not verified."""
    from documents.models import VerificationStatus
    unverified = 0
    for doc in application.documents.prefetch_related('versions'):
        version = doc.current_version()
        if version and version.verification_status != VerificationStatus.VERIFIED:
            unverified += 1
    return {'status': UNVERIFIED, 'count': unverified} if unverified else None


def default_remarks(application, documents):
    parts = []
    if application.status in STATUS_REMARKS:
        parts.append(STATUS_REMARKS[application.status])
    if documents:
        parts.append('Document verification pending')
    return '; '.join(parts)


def build_row(application):
    """(values without A/T, field flags, document flag) for one application."""
    facts = list(application.facts.exclude(review_status=FactReviewStatus.SUPERSEDED)
                 .exclude(fact_type=FactType.QUALIFICATION_ASSESSMENT))
    row = Row()
    _identity(row, facts)
    _license(row, facts)
    _education(row, facts)
    _relevant(row, 'R', facts, FactType.EXPERIENCE, _experience_text)
    _relevant(row, 'S', facts, FactType.TRAINING, _training_text)
    return row.values, row.flags, document_flag(application)


def sort_key(application, values):
    def plain(column):
        return values.get(column, '').removesuffix(UNVERIFIED_MARK).casefold()
    return (plain('B'), plain('C'), plain('D'), application.submitted_at, str(application.pk))
