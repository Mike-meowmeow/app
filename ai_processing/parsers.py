"""Rule-based proposals of qualification items from document text (13.3).

Input is the page text read by the engine; output is a list of proposed items
whose values use the field names in recruitment/fact_schema.py. These are
proposals for HR, not facts: anything the rules cannot read with reasonable
certainty is left empty rather than guessed (for example a graduation year
without a day is not turned into a date), and HR accepts, corrects, links, or
rejects every item.

Pure Python (regular expressions). spaCy is optional and only used to find an
organization name when the keyword rules find none.
"""

import re
from datetime import date

PARSER_VERSION = 'rules-1'

MONTHS = {m: i for i, m in enumerate(
    ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august',
     'september', 'october', 'november', 'december'], start=1)}
MONTH_ALIASES = {**MONTHS, **{m[:3]: i for m, i in MONTHS.items()}, 'sept': 9}
MONTH_RE = (r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|'
            r'sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?')
DATE_RE = re.compile(
    rf'(?P<iso>\b\d{{4}}-\d{{2}}-\d{{2}}\b)'
    rf'|(?P<mdy>\b{MONTH_RE}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b)'
    rf'|(?P<dmy>\b\d{{1,2}}(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?{MONTH_RE},?\s+\d{{4}}\b)'
    rf'|(?P<num>\b\d{{1,2}}/\d{{1,2}}/\d{{4}}\b)',
    re.I)
RANGE_RE = re.compile(rf'(?P<month>{MONTH_RE})\s+(?P<d1>\d{{1,2}})\s*(?:-|–|to)\s*'
                      rf'(?P<d2>\d{{1,2}}),?\s+(?P<year>\d{{4}})', re.I)

INSTITUTION_RE = re.compile(r'\b(University|College|Institute|Academy|Polytechnic|'
                            r'School|Seminary)\b', re.I)
DEGREE_RE = re.compile(r'\b(Bachelor|Master|Doctor(?:ate)?)\s+(?:of|in)\s+\S.*', re.I)
PHD_RE = re.compile(r'\bPh\.?\s?D\.?\b')
DEGREE_STOP_RE = re.compile(
    r'\s+(?:on|conferred|awarded|given|granted|dated|with\s+(?:honors|highest|high))\b'
    r'|\s{2,}|\s*[;|]', re.I)
GRADUATION_RE = re.compile(
    r'(?:date\s+of\s+graduation|date\s+graduated|graduated(?:\s+on)?|conferred(?:\s+on)?|'
    r'awarded(?:\s+on)?|given\s+(?:this|on)|done\s+this|this)\W{0,3}(?P<rest>.{0,60})', re.I)
LABEL_RE = re.compile(r'^(?:name\s+of\s+(?:school|institution)|school|institution|degree|'
                      r'course|program)\s*[:\-]\s*', re.I)


def clean(text, limit=255):
    text = re.sub(r'\s+', ' ', text or '').strip(' .,;:-–"“”\'')
    return text[:limit]


def smart_title(text):
    """BACHELOR OF SCIENCE IN EDUCATION -> Bachelor of Science in Education."""
    if not text.isupper():
        return text
    small = {'of', 'in', 'and', 'the', 'for', 'major', 'on', 'at'}
    words = text.lower().split()
    return ' '.join(w if (w in small and i) else w.capitalize() for i, w in enumerate(words))


def parse_date(text):
    """First full date in text as an ISO string, or None. Month-and-year only
    is not a full date and returns None."""
    if not text:
        return None
    match = DATE_RE.search(text)
    if not match:
        return None
    raw = match.group(0)
    try:
        if match.group('iso'):
            y, m, d = (int(p) for p in raw.split('-'))
        elif match.group('num'):
            m, d, y = (int(p) for p in raw.split('/'))
        else:
            words = re.findall(r'[a-z]+|\d+', raw.lower())
            numbers = [int(w) for w in words if w.isdigit()]
            month = next(MONTH_ALIASES[w.rstrip('.')] for w in words
                         if w.rstrip('.') in MONTH_ALIASES)
            y = next(n for n in numbers if n > 31)
            d = next(n for n in numbers if n <= 31)
            m = month
        return date(y, m, d).isoformat()
    except (ValueError, StopIteration, KeyError):
        return None


def all_dates(text):
    found = []
    for match in DATE_RE.finditer(text or ''):
        value = parse_date(match.group(0))
        if value:
            found.append(value)
    return found


def date_range(text):
    """(start, end) from "March 3-5, 2019" or "from <date> to <date>"."""
    match = RANGE_RE.search(text or '')
    if match:
        month = MONTH_ALIASES.get(match.group('month').lower().rstrip('.')[:3]) or \
            MONTH_ALIASES.get(match.group('month').lower().rstrip('.'))
        year = int(match.group('year'))
        try:
            return (date(year, month, int(match.group('d1'))).isoformat(),
                    date(year, month, int(match.group('d2'))).isoformat())
        except (TypeError, ValueError):
            pass
    dates = all_dates(text)
    if len(dates) >= 2:
        return dates[0], dates[1]
    if len(dates) == 1:
        return dates[0], dates[0]
    return None, None


class Page:
    """Normalized lines of one page, with line lookup for source locations."""

    def __init__(self, number, text, method=''):
        self.number = number
        self.method = method
        self.lines = [clean(line, 1000) for line in (text or '').splitlines()]
        self.lines = [line for line in self.lines if line]
        self.text = '\n'.join(self.lines)

    def line_of(self, offset):
        return self.text.count('\n', 0, offset) + 1

    def location(self, start, end=None):
        first = self.line_of(start)
        last = self.line_of(end if end is not None else start)
        return {'page': self.number, 'lines': [first, last], 'method': self.method}


def item(item_type, page, start, end, value, confidence):
    value = {k: v for k, v in value.items()}
    raw = page.text[max(0, start):end] if end else page.text[start:start + 200]
    return {
        'item_type': item_type,
        'raw_text': clean(raw, 2000),
        'proposed_value': value,
        'page_number': page.number,
        'source_location': page.location(start, end),
        'confidence': round(min(max(confidence, 0.0), 0.95), 3),
    }


def find_institution(pages, nlp=None):
    candidates = []
    for page in pages:
        for line in page.lines:
            if INSTITUTION_RE.search(line) and not DEGREE_RE.search(line):
                name = clean(LABEL_RE.sub('', line))
                if 4 <= len(name) <= 150:
                    candidates.append(name)
    for keyword in ('university', 'college', 'institute'):
        for name in candidates:
            if keyword in name.lower() and not name.lower().startswith(f'{keyword} of '):
                return smart_title(name)
    if candidates:
        return smart_title(candidates[0])
    if nlp is not None:
        for page in pages:
            for ent in nlp(page.text).ents:
                if ent.label_ == 'ORG' and INSTITUTION_RE.search(ent.text):
                    return clean(ent.text)
    return None


def degree_level(program):
    lowered = program.lower()
    if lowered.startswith('bachelor'):
        return 'bachelor'
    if lowered.startswith('master'):
        return 'master'
    if lowered.startswith('doctor') or PHD_RE.search(program):
        return 'doctorate'
    return 'other'


# --------------------------------------------------------------------------
# Education: transcript of records, diploma, resume
# --------------------------------------------------------------------------

def _degrees(page):
    """[(start, end, program)] for degree phrases on a page."""
    found = []
    for index, line in enumerate(page.lines):
        match = DEGREE_RE.search(line)
        if not match:
            continue
        program = DEGREE_STOP_RE.split(match.group(0))[0]
        date_match = DATE_RE.search(program)
        if date_match:
            program = program[:date_match.start()]
        program = clean(program, 200)
        following = page.lines[index + 1] if index + 1 < len(page.lines) else ''
        if 'major' not in program.lower() and re.match(r'^(?:major\s*(?:in|:)?)\s+\S', following,
                                                         re.I):
            program = clean(f'{program} {following}', 200)
        if len(program.split()) < 3:
            continue
        start = page.text.find(line)
        found.append((start, start + len(line), smart_title(program)))
    return found


def education_items(pages, document_type, nlp=None):
    items, seen = [], set()
    institution = find_institution(pages, nlp)
    full_text = '\n'.join(p.text for p in pages)
    graduation = None
    for match in GRADUATION_RE.finditer(full_text):
        graduation = parse_date(match.group('rest'))
        if graduation:
            break
    if document_type == 'DIPLOMA' and not graduation:
        dates = all_dates(full_text)
        graduation = dates[-1] if dates else None
    for page in pages:
        for start, end, program in _degrees(page):
            key = program.lower()
            if key in seen:
                continue
            seen.add(key)
            line = page.text[start:end]
            own_institution = institution
            if document_type == 'RESUME':
                # A resume lists several schools: only use one on the same line.
                own_institution = next((clean(part) for part in re.split(r'[,–\-|]', line)
                                        if INSTITUTION_RE.search(part)
                                        and not DEGREE_RE.search(part)), None)
            completed = document_type == 'DIPLOMA' or (document_type == 'TOR' and graduation)
            value = {
                'degree_level': degree_level(program),
                'program': program,
                'institution': own_institution,
                'completion_status': 'completed' if completed else None,
                'completed_on': graduation if completed and document_type != 'RESUME' else None,
            }
            confidence = 0.5 + (0.2 if own_institution else 0) + (0.2 if completed else 0)
            if document_type == 'RESUME':
                confidence -= 0.2
            items.append(item('education', page, start, end, value, confidence))
    return items


# --------------------------------------------------------------------------
# Training certificates
# --------------------------------------------------------------------------

TRAINING_TITLE_RE = re.compile(
    r'(?:having\s+)?(?:successfully\s+)?(?:completed|attended|participated\s+in|'
    r'completion\s+of|participation\s+in|attendance\s+(?:at|in))\s+(?:the\s+)?'
    r'["“”]?(?P<title>[^"“”\n]{6,150}?(?:\n[^"“”\n]{3,100}?)??)["“”]?\s*'
    r'(?=[,.]|\s(?:held|conducted|organized|sponsored|given|on|from|at|last|with)\b|\n|$)',
    re.I)
QUOTED_RE = re.compile(r'["“](?P<title>[^"”]{6,150})["”]')
HOURS_RE = re.compile(r'(?P<hours>\d{1,3}(?:\.\d{1,2})?)\s*-?\s*(?:training\s+|contact\s+)?'
                      r'(?:hours|hrs)\b', re.I)
PROVIDER_RE = re.compile(r'(?:conducted|organized|sponsored|given|presented|offered)\s+by\s+'
                         r'(?:the\s+)?(?P<provider>[^.,\n]{3,120})', re.I)


def training_items(pages, document_type, nlp=None):
    items = []
    for page in pages:
        # A quoted title is the most reliable; otherwise "completed/attended ...".
        match = QUOTED_RE.search(page.text) or TRAINING_TITLE_RE.search(page.text)
        if not match:
            continue
        title = smart_title(clean(match.group('title'), 200))
        if len(title.split()) < 2:
            continue
        hours = HOURS_RE.search(page.text)
        start_date, end_date = date_range(page.text[match.end():] or page.text)
        provider = PROVIDER_RE.search(page.text)
        provider_name = clean(provider.group('provider'), 200) if provider else \
            find_institution([page], nlp)
        value = {
            'title': title,
            'provider': provider_name,
            'hours': hours.group('hours') if hours else None,
            'start_date': start_date,
            'end_date': end_date,
        }
        confidence = 0.5 + (0.15 if hours else 0) + (0.15 if start_date else 0) + \
            (0.1 if provider_name else 0)
        items.append(item('training', page, match.start(), match.end(), value, confidence))
    return items


# --------------------------------------------------------------------------
# Certificates of employment
# --------------------------------------------------------------------------

DATE_TEXT = (r'(?:[A-Za-z]{3,9}\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}'
             r'|\d{1,2}(?:st|nd|rd|th)?\s+(?:day\s+of\s+)?[A-Za-z]{3,9},?\s+\d{4}'
             r'|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2}|[A-Za-z]{3,9}\s+\d{4})')
EMPLOYMENT_RE = re.compile(
    r'(?:employed|worked|served|working|serving|connected)\s+(?:with\s+us\s+)?as\s+'
    r'(?:an?\s+)?(?P<position>[A-Z][^,.\n]{2,80}?)'
    r'(?:\s+(?:at|in|with|of)\s+(?:the\s+)?(?P<org>[A-Z][^,.\n]{2,120}?))?'
    rf'\s+(?:from|since)\s+(?P<start>{DATE_TEXT})'
    rf'(?:\s+(?:to|until|up\s+to)\s+(?P<end>the\s+present|present|date|{DATE_TEXT}))?',
    re.I)


def experience_items(pages, document_type, nlp=None):
    items = []
    organization = find_institution(pages, nlp)
    for page in pages:
        for match in EMPLOYMENT_RE.finditer(page.text):
            end_text = (match.group('end') or '').lower()
            value = {
                'position': smart_title(clean(match.group('position'), 200)),
                'organization': clean(match.group('org'), 200) if match.group('org')
                else organization,
                'start_date': parse_date(match.group('start')),
                'end_date': None if end_text in ('', 'present', 'date', 'the present')
                else parse_date(match.group('end')),
                'duties': None,
            }
            confidence = 0.5 + (0.2 if value['organization'] else 0) + \
                (0.2 if value['start_date'] else 0)
            items.append(item('experience', page, match.start(), match.end(), value, confidence))
    return items


# --------------------------------------------------------------------------
# PRC license and eligibility certificates
# --------------------------------------------------------------------------

PROFESSIONS = ('Professional Teacher', 'Civil Engineer', 'Electrical Engineer',
               'Mechanical Engineer', 'Electronics Engineer', 'Chemical Engineer',
               'Registered Nurse', 'Certified Public Accountant', 'Architect', 'Librarian',
               'Psychometrician', 'Psychologist', 'Social Worker', 'Agriculturist',
               'Criminologist', 'Chemist', 'Medical Technologist', 'Pharmacist')
LICENSE_NO_RE = re.compile(r'(?:registration|license|licence)\s*(?:no\.?|number|#)\s*[:\-]?\s*'
                           r'(?P<number>\d{5,8})\b', re.I)
ISSUED_RE = re.compile(r'(?:registration\s+date|date\s+of\s+(?:registration|issue)|'
                       r'date\s+issued|issued\s+on)\s*[:\-]?\s*(?P<rest>.{0,40})', re.I)
EXPIRES_RE = re.compile(r'(?:valid\s+until|expir(?:y|ation|es)(?:\s+date)?|valid\s+thru)\s*'
                        r'[:\-]?\s*(?P<rest>.{0,40})', re.I)
ELIGIBILITY_RE = re.compile(
    r'(Career\s+Service\s+(?:Professional|Sub-?\s?professional)'
    r'|Licensure\s+Examination\s+for\s+(?:Professional\s+)?Teachers'
    r'|Board\s+Examination\s+for\s+[A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+){0,3}'
    r'|(?:RA|R\.A\.)\s*1080)', re.I)
RATING_RE = re.compile(r'(?:rating|general\s+average|grade)\s*(?:of)?\s*[:\-]?\s*'
                       r'(?P<rating>\d{2,3}(?:\.\d{1,2})?)\s*%?', re.I)
EXAM_DATE_RE = re.compile(r'(?:given|held|taken|conducted|examination)\s+(?:on\s+)?'
                          r'(?P<rest>.{0,40})', re.I)


def license_items(pages, document_type, nlp=None):
    items = []
    for page in pages:
        number = LICENSE_NO_RE.search(page.text)
        profession = next((m for p in PROFESSIONS
                           if (m := re.search(rf'\b{p}\b', page.text, re.I))), None)
        if not number and not profession:
            continue
        issued = ISSUED_RE.search(page.text)
        expires = EXPIRES_RE.search(page.text)
        is_prc = bool(re.search(r'Professional\s+Regulation\s+Commission|\bPRC\b', page.text,
                                re.I))
        value = {
            'profession': smart_title(profession.group(0)) if profession else None,
            'license_number': number.group('number') if number else None,
            'issuing_body': 'Professional Regulation Commission' if is_prc else None,
            'issued_on': parse_date(issued.group('rest')) if issued else None,
            'expires_on': parse_date(expires.group('rest')) if expires else None,
        }
        anchors = [m for m in (number, profession) if m]
        start = min(m.start() for m in anchors)
        end = max(m.end() for m in anchors)
        confidence = 0.4 + (0.25 if number else 0) + (0.2 if profession else 0) + \
            (0.1 if value['expires_on'] else 0)
        items.append(item('license', page, start, end, value, confidence))
    return items


def eligibility_items(pages, document_type, nlp=None):
    items = []
    for page in pages:
        match = ELIGIBILITY_RE.search(page.text)
        if not match:
            continue
        name = clean(match.group(0))
        if re.match(r'(?:RA|R\.A\.)\s*1080', name, re.I):
            name = 'RA 1080 (Licensure Examination for Teachers)'
        rating = RATING_RE.search(page.text)
        exam = EXAM_DATE_RE.search(page.text[match.end():])
        value = {
            'eligibility_type': smart_title(name) if name.isupper() else name,
            'rating': rating.group('rating') if rating else None,
            'date_obtained': parse_date(exam.group('rest')) if exam else None,
        }
        confidence = 0.55 + (0.2 if rating else 0) + (0.15 if value['date_obtained'] else 0)
        items.append(item('eligibility', page, match.start(), match.end(), value, confidence))
    return items


PARSERS = {
    'TOR': (education_items,),
    'DIPLOMA': (education_items,),
    'RESUME': (education_items,),
    'TRAINING': (training_items,),
    'EMPLOYMENT': (experience_items,),
    'PRC': (license_items, eligibility_items),
}


def propose(document_type, raw_pages, nlp=None):
    """(items, warnings) for one document. raw_pages: [{'page', 'text', 'method'}]."""
    pages = [Page(p['page'], p.get('text', ''), p.get('method', '')) for p in raw_pages]
    warnings = []
    if not any(page.text for page in pages):
        return [], ['No readable text was found in this document.']
    parsers = PARSERS.get(document_type)
    if not parsers:
        return [], [f'No item rules exist yet for this document type ({document_type}); '
                    'the text is shown for manual review.']
    items = []
    for parser in parsers:
        items += parser(pages, document_type, nlp)
    if not items:
        warnings.append('No qualification items were recognized; review the text manually.')
    for found in items:
        empty = [k for k, v in found['proposed_value'].items() if v in (None, '')]
        if empty:
            found['source_location']['unread_fields'] = empty
    return items, warnings
