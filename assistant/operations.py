"""Operations the staff assistant can carry out, always after the person
confirms a card that says exactly what will happen.

How it is kept safe:
- The language model only interprets the request: which operation and the
  words used for the vacancy or application. It never sees records. Records
  are found here, with the signed-in person's own visibility rules, and every
  card, count, and list is built by this code, not written by the model.
- Only the operations below exist. Decisions, shortlisting, hiring, roster
  approval or release, document verification, accepting or rejecting facts,
  role changes, and deletions are not available here, by design (FR-010,
  FR-019, and the AI rules: humans perform those on their own pages).
- A card carries a signed, short-lived token naming the person, the operation,
  and the record IDs. On Confirm the permission checks run again and the
  normal service runs, with its usual audit entry, as that person.
"""

import re

from django.core import signing
from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse
from django.utils.html import escape, format_html, format_html_join

from accounts import roles
from audit import services as audit
from recruitment import application_selectors as access
from recruitment import selectors
from recruitment.models import Application, ApplicationStatus

TOKEN_SALT = 'assistant-operation'
TOKEN_MAX_AGE = 600  # seconds a confirmation card stays valid
MAX_CHOICES = 5
MAX_LIST = 15

# key: (label, URL name, roles that may open it)
PAGES = {
    'hr_workspace': ('HR workspace', 'core:workspace_hr', (roles.HR,)),
    'verification_queue': ('Document verification queue', 'recruitment:verification_queue',
                           (roles.HR,)),
    'ai_extraction_results': ('AI extraction results', 'ai_processing:results', (roles.HR,)),
    'vacancies': ('Vacancies and requirements', 'recruitment:vacancy_list', (roles.HR,)),
    'position_catalog': ('Position catalog', 'recruitment:position_list', (roles.HR,)),
    'search_applications': ('Search applications', 'recruitment:application_search',
                            (roles.HR,)),
    'analytics': ('Recruitment analytics and reports', 'analytics:dashboard', (roles.HR,)),
    'decisions_pending': ('Decisions awaiting release', 'recruitment:decisions_pending',
                          (roles.HR,)),
    'user_accounts': ('User accounts and roles', 'accounts:user_list', (roles.SYSTEM_ADMIN,)),
    'backups': ('Backup and restore monitoring', 'operations:overview', (roles.SYSTEM_ADMIN,)),
    'audit_trail': ('Audit trail', 'audit:list', (roles.SYSTEM_ADMIN,)),
    'email_deliveries': ('Email deliveries', 'notifications:deliveries', (roles.SYSTEM_ADMIN,)),
    'roster_templates': ('Official roster templates', 'matrix:templates',
                         (roles.SYSTEM_ADMIN,)),
}
VACANCY_SECTIONS = {
    'details': ('vacancy page', 'recruitment:vacancy_detail'),
    'roster': ('official roster', 'matrix:vacancy_rosters'),
    'committees': ('committees and interviews', 'committee:vacancy_committees'),
}
APPLICATION_SECTIONS = {
    'review': ('review page', 'recruitment:application_review'),
    'selection': ('selection and decisions page', 'recruitment:application_selection'),
}
STATUS_WORDS = {s.value: s.label for s in ApplicationStatus if s != ApplicationStatus.DRAFT}
# Instructions the assistant refuses, because staff perform them on their own
# pages (questions about how to do them get the help answer instead).
FORBIDDEN = re.compile(
    r'\b(shortlist\w*|hire|hiring|appoint\w*|reject\w*|approve\w*|release\w*|verify|'
    r'accept\w*|delete\w*|withdraw\w*|deactivat\w*|(?:assign|change|give)\w*\s+(?:\w+\s+)?roles?)\b',
    re.I)
QUESTION_START = re.compile(r'\s*(how|what|where|when|why|which|who|can|could|should|is|are|do|'
                            r'does|paano|ano|saan|bakit)\b', re.I)
REFUSAL = ('I cannot do that for you. Decisions, shortlisting, hiring, approvals, releases, '
           'document verification, accepting or rejecting facts, role changes, and deletions '
           'are done by staff on their own pages.')


def forbidden_instruction(question):
    """True for an instruction to do something only staff may do themselves."""
    return bool(FORBIDDEN.search(question)) and not QUESTION_START.match(question)


class NotFound(Exception):
    pass


# --------------------------------------------------------------------------
# Tools offered to the model, by role
# --------------------------------------------------------------------------

def _tool(name, description, properties=None, required=()):
    return {'type': 'function', 'function': {
        'name': name, 'description': description,
        'parameters': {'type': 'object', 'properties': properties or {},
                       'required': list(required)}}}


def tools_for(user_roles):
    pages = [k for k, (_, _, allowed) in PAGES.items() if set(allowed) & set(user_roles)]
    tools = [_tool('open_page', 'Open one of the main staff pages.',
                   {'page': {'type': 'string', 'enum': pages}}, ['page'])] if pages else []
    if roles.HR in user_roles:
        vacancy = {'type': 'string', 'description': 'Exactly the words the person typed for '
                                                    'the vacancy: a reference starting with VAC- '
                                                    'or words from its position title.'}
        application = {'type': 'string', 'description': 'Exactly the words the person typed '
                                                        'for the application: a reference '
                                                        'starting with APP- or the applicant '
                                                        'name.'}
        tools += [
            _tool('open_vacancy', 'Open a vacancy, its official roster, or its committees.',
                  {'vacancy': vacancy,
                   'section': {'type': 'string', 'enum': list(VACANCY_SECTIONS)}},
                  ['vacancy']),
            _tool('open_application', "Open an application's review page or its selection "
                                      'and decisions page.',
                  {'application': application,
                   'section': {'type': 'string', 'enum': list(APPLICATION_SECTIONS)}},
                  ['application']),
            _tool('show_work_summary', 'Show counts of my pending work: documents waiting for '
                                       'verification, AI items waiting for review, decisions '
                                       'awaiting release, and applications by status.'),
            _tool('list_applications', 'List applications, optionally for one vacancy and/or '
                                       'with one status.',
                  {'vacancy': vacancy,
                   'status': {'type': 'string', 'enum': list(STATUS_WORDS)}}),
            _tool('run_qualification_checks', 'Run the advisory qualification checks for one '
                                              'application.', {'application': application},
                  ['application']),
            _tool('run_competency_matching', 'Queue advisory SBERT competency matching for one '
                                             'application.', {'application': application},
                  ['application']),
            _tool('retry_failed_ai', 'Retry the failed AI document reading jobs of one '
                                     'application.', {'application': application},
                  ['application']),
        ]
    return tools


# --------------------------------------------------------------------------
# Finding records with the person's own permissions
# --------------------------------------------------------------------------

REFERENCE = re.compile(r'\b(VAC|APP)-([0-9A-F]{8})\b', re.I)


PREFIX_WORDS = {'app', 'vac'}  # a stray "APP-" or "VAC-" the model put before a name


def _words(text):
    return [w for w in re.findall(r'[a-z0-9ñ]+', (text or '').lower())
            if len(w) > 1 and w not in PREFIX_WORDS]


def find_vacancies(user, text):
    vacancies = list(selectors.visible_vacancies(user).order_by('-created_at')[:500])
    match = REFERENCE.search(text or '')
    if match and match.group(1).upper() == 'VAC':
        return [v for v in vacancies if v.reference == f'VAC-{match.group(2).upper()}']
    words = [w for w in _words(text) if w not in {'vacancy', 'the', 'for', 'of', 'part',
                                                    'time', 'instructor', 'position'}]
    if not words:
        return vacancies if len(vacancies) == 1 else []
    return [v for v in vacancies if all(w in v.position_title.lower() for w in words)]


def find_applications(user, text, vacancy=None):
    applications = access.visible_applications(user)
    if vacancy is not None:
        applications = applications.filter(vacancy=vacancy)
    applications = list(applications.order_by('-submitted_at')[:2000])
    match = REFERENCE.search(text or '')
    if match and match.group(1).upper() == 'APP':
        return [a for a in applications if a.reference == f'APP-{match.group(2).upper()}']
    words = [w for w in _words(text) if w not in {'application', 'applicant', 'of', 'the',
                                                    'for', 'mr', 'ms', 'mrs', 'dr'}]
    if not words:
        return []

    def name(a):
        u = a.applicant.user
        return f'{u.first_name} {u.middle_name or ""} {u.last_name} {u.username}'.lower()
    return [a for a in applications if all(w in name(a) for w in words)]


def grounded(value, question):
    """True when every reference and word the model passed appears in what the
    person typed. The model must copy, never invent or complete, a value."""
    if not value:
        return False
    asked_refs = {m.group(0).upper() for m in REFERENCE.finditer(question)}
    refs = {m.group(0).upper() for m in REFERENCE.finditer(value)}
    if refs:
        return refs <= asked_refs
    return set(_words(value)) <= set(_words(question))


def find_applications_in_question(user, question):
    """Applications whose applicant's first or last name is a word the person typed."""
    asked = {w for w in _words(question) if len(w) > 2}
    found = []
    for a in access.visible_applications(user).select_related('applicant__user')[:2000]:
        u = a.applicant.user
        if asked & (set(_words(u.last_name)) | set(_words(u.first_name))):
            found.append(a)
    return found


def _application_label(a):
    u = a.applicant.user
    return f'{a.reference} ({u.last_name}, {u.first_name}), {a.vacancy.position_title}'


def _vacancy_label(v):
    return f'{v.reference}, {v.position_title}'


# --------------------------------------------------------------------------
# Building confirmation cards (nothing runs here)
# --------------------------------------------------------------------------

def _card(user, op, summary, **ids):
    token = signing.dumps({'u': str(user.pk), 'op': op, 'p': ids}, salt=TOKEN_SALT)
    return {'summary': summary, 'token': token}


def _choices(found, describe, kind):
    if not found:
        raise NotFound(f'I could not find a {kind} matching that among the ones you can see. '
                       f'Try its reference (for example {"APP" if kind == "application" else "VAC"}'
                       '-1A2B3C4D) or other words.')
    if len(found) > MAX_CHOICES:
        raise NotFound(f'{len(found)} {kind}s match. Please be more specific, for example '
                       'with the reference.')
    return found


def _application_cards(user, name, args, found):
    if name == 'open_application':
        section = args.get('section') if args.get('section') in APPLICATION_SECTIONS \
            else 'review'
        verb = f'Open the {APPLICATION_SECTIONS[section][0]} of'
        extra = {'section': section}
    else:
        verb = {'run_qualification_checks': 'Run the advisory qualification checks for',
                'run_competency_matching': 'Queue advisory competency matching for',
                'retry_failed_ai': 'Retry the failed AI reading jobs of'}[name]
        extra = {}
    return ('Please confirm:' if len(found) == 1 else 'Which application?',
            [_card(user, name, f'{verb} {_application_label(a)}', application=str(a.pk),
                   **extra) for a in found])


def prepare(user, name, arguments, question=''):
    """(message, [cards]) for a tool call, or raises NotFound / PermissionDenied.
    A vacancy or application value the person did not type is not trusted."""
    user_roles = roles.role_names(user)
    allowed = {t['function']['name'] for t in tools_for(user_roles)}
    if name not in allowed:
        raise PermissionDenied
    args = {k: str(v)[:120] for k, v in (arguments or {}).items() if v not in (None, '')}
    for key in ('vacancy', 'application'):
        if key in args and question and not grounded(args[key], question):
            args.pop(key)  # invented by the model: search what was typed instead
    if name in ('open_application', 'run_qualification_checks', 'run_competency_matching',
                'retry_failed_ai') and 'application' not in args:
        typed = REFERENCE.search(question or '')
        if typed and typed.group(1).upper() == 'APP':
            args['application'] = typed.group(0)
        else:
            found = _choices(find_applications_in_question(user, question), _application_label,
                             'application')
            return _application_cards(user, name, args, found)

    if name == 'open_page':
        page = args.get('page')
        if page not in PAGES or not set(PAGES[page][2]) & set(user_roles):
            raise NotFound('I can only open the main pages of your workspace.')
        label = PAGES[page][0]
        return 'Shall I open this page?', [_card(user, name, f'Open {label}', page=page)]

    if name == 'open_vacancy':
        section = args.get('section') if args.get('section') in VACANCY_SECTIONS else 'details'
        found = _choices(find_vacancies(user, args.get('vacancy', '')), _vacancy_label,
                         'vacancy')
        return ('Shall I open it?' if len(found) == 1 else 'Which vacancy?',
                [_card(user, name, f'Open the {VACANCY_SECTIONS[section][0]} of '
                                   f'{_vacancy_label(v)}', vacancy=str(v.pk), section=section)
                 for v in found])

    if name in ('open_application', 'run_qualification_checks', 'run_competency_matching',
                'retry_failed_ai'):
        found = _choices(find_applications(user, args.get('application', '')),
                         _application_label, 'application')
        return _application_cards(user, name, args, found)

    if name == 'show_work_summary':
        return 'Shall I count your pending work?', [
            _card(user, name, 'Show counts of your pending work in the vacancies you handle')]

    if name == 'list_applications':
        status = args.get('status') if args.get('status') in STATUS_WORDS else ''
        vacancy_text = args.get('vacancy', '')
        vacancies = _choices(find_vacancies(user, vacancy_text), _vacancy_label, 'vacancy') \
            if vacancy_text else [None]
        status_text = f' with status {STATUS_WORDS[status]}' if status else ''
        return ('Shall I list them?' if len(vacancies) == 1 else 'Which vacancy?',
                [_card(user, name, f'List applications{status_text}'
                                   + (f' for {_vacancy_label(v)}' if v else
                                      ' in the vacancies you handle'),
                       vacancy=str(v.pk) if v else '', status=status) for v in vacancies])
    raise NotFound('I cannot do that.')


# --------------------------------------------------------------------------
# Running a confirmed card
# --------------------------------------------------------------------------

def _load(token, user):
    try:
        data = signing.loads(token, salt=TOKEN_SALT, max_age=TOKEN_MAX_AGE)
    except signing.SignatureExpired:
        raise ValidationError('This confirmation expired. Ask again.')
    except signing.BadSignature:
        raise PermissionDenied
    if data.get('u') != str(user.pk):
        raise PermissionDenied
    return data['op'], data.get('p', {})


def _visible_application(user, pk):
    try:
        return access.visible_applications(user).get(pk=pk)
    except (Application.DoesNotExist, ValidationError, ValueError):
        raise PermissionDenied


def _visible_vacancy(user, pk):
    try:
        return selectors.visible_vacancies(user).get(pk=pk)
    except Exception:
        raise PermissionDenied


def _link(url, text):
    return format_html('<a href="{}">{}</a>', url, text)


def run(user, token):
    """Carry out a confirmed card. Returns {'redirect': url} or {'html': ...}."""
    op, p = _load(token, user)
    user_roles = roles.role_names(user)
    if op not in {t['function']['name'] for t in tools_for(user_roles)}:
        raise PermissionDenied  # roles changed since the card was made

    if op == 'open_page':
        page = PAGES.get(p.get('page'))
        if not page or not set(page[2]) & set(user_roles):
            raise PermissionDenied
        return {'redirect': reverse(page[1])}

    if op == 'open_vacancy':
        vacancy = _visible_vacancy(user, p.get('vacancy'))
        section = VACANCY_SECTIONS.get(p.get('section'), VACANCY_SECTIONS['details'])
        return {'redirect': reverse(section[1], args=[vacancy.pk])}

    if op == 'open_application':
        application = _visible_application(user, p.get('application'))
        section = APPLICATION_SECTIONS.get(p.get('section'), APPLICATION_SECTIONS['review'])
        return {'redirect': reverse(section[1], args=[application.pk])}

    if op == 'show_work_summary':
        return {'html': _work_summary(user)}

    if op == 'list_applications':
        vacancy = _visible_vacancy(user, p['vacancy']) if p.get('vacancy') else None
        return {'html': _application_list(user, vacancy, p.get('status') or '')}

    application = _visible_application(user, p.get('application'))
    if not access.can_review(user, application):
        raise PermissionDenied
    audit.record('assistant.operation_confirmed', entity=application, actor=user,
                 changes={'operation': op})
    if op == 'run_qualification_checks':
        from ai_processing import screening
        check, created = screening.run_checks(user, application)
        url = reverse('recruitment:application_review',
                      args=[application.pk]) + '?checked=1#qualifications'
        text = (f'Qualification checks done: {check.get_overall_status_display()} overall '
                '(advisory).' if created else 'Nothing changed since the last check.')
        return {'html': format_html('{} {}', text, _link(url, 'Open the results'))}
    if op == 'run_competency_matching':
        from ai_processing import matching
        job, created = matching.queue_matching(user, application)
        url = reverse('ai_processing:matches', args=[application.pk, job.pk])
        text = ('Competency matching queued; the AI worker runs it in a few seconds.'
                if created else 'Nothing changed since the last matching run.')
        return {'html': format_html('{} {}', text, _link(url, 'Open the results'))}
    if op == 'retry_failed_ai':
        from ai_processing import services as ai
        from ai_processing.models import AIJob, JobStatus, JobType
        failed = AIJob.objects.filter(application=application, status=JobStatus.FAILED,
                                      job_type=JobType.DOCUMENT_EXTRACTION,
                                      invalidated_at__isnull=True)
        retried, problems = 0, []
        for job in failed:
            try:
                ai.retry(user, job)
                retried += 1
            except ValidationError as exc:
                problems.extend(exc.messages)
        text = (f'Queued {retried} failed AI job{"s" if retried != 1 else ""} again.'
                if retried else 'There were no failed AI jobs to retry.')
        if problems:
            text += ' Not retried: ' + ' '.join(problems)
        return {'html': escape(text)}
    raise PermissionDenied


def _work_summary(user):
    from ai_processing.models import AIJob, ExtractionItem, ItemReviewStatus, JobType
    from documents import services as documents
    from recruitment import decision_services as decisions
    visible = access.visible_applications(user)
    items = ExtractionItem.objects.filter(
        review_status=ItemReviewStatus.PENDING,
        extraction__job__job_type=JobType.DOCUMENT_EXTRACTION,
        extraction__job__invalidated_at__isnull=True,
        extraction__job__application__in=visible.values('pk')).count()
    rows = [
        ('Documents waiting for verification', len(documents.pending_verifications(user)),
         reverse('recruitment:verification_queue')),
        ('AI items waiting for HR review', items, reverse('ai_processing:results')),
        ('Decisions awaiting release', len(decisions.pending_release(user)),
         reverse('recruitment:decisions_pending')),
    ]
    by_status = {s: visible.filter(status=s).count()
                 for s in (ApplicationStatus.SUBMITTED, ApplicationStatus.UNDER_REVIEW,
                           ApplicationStatus.CORRECTION_REQUESTED)}
    html = format_html_join('', '<li>{}: <strong>{}</strong> · <a href="{}">open</a></li>', rows)
    html += format_html_join('', '<li>Applications {}: <strong>{}</strong></li>',
                             ((ApplicationStatus(s).label.lower(), n)
                              for s, n in by_status.items()))
    return format_html('Your pending work (vacancies you handle):<ul class="mb-0">{}</ul>', html)


def _application_list(user, vacancy, status):
    applications = access.visible_applications(user)
    if vacancy is not None:
        applications = applications.filter(vacancy=vacancy)
    if status:
        applications = applications.filter(status=status)
    total = applications.count()
    shown = list(applications.select_related('applicant__user', 'vacancy')
                 .order_by('-submitted_at')[:MAX_LIST])
    if not shown:
        return escape('No applications match.')
    rows = format_html_join('', '<li><a href="{}">{}</a> · {}</li>', (
        (reverse('recruitment:application_review', args=[a.pk]), _application_label(a),
         a.get_status_display()) for a in shown))
    more = format_html(' (showing the latest {})', MAX_LIST) if total > MAX_LIST else ''
    return format_html('{} application{}{}:<ul class="mb-0">{}</ul>', total,
                       '' if total == 1 else 's', more, rows)
