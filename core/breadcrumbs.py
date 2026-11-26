"""Breadcrumb trails for every page, in one place.

PAGES maps a URL name to its label and parent page. A trail is built only while
rendering a page the user was already allowed to open. Labels use position
titles and record references, never applicant names. Sign-in pages show no trail.

Staff trails include the role workspace, e.g. Home > HR > Vacancies. Someone with
several roles sees the workspace they opened last when it covers the page;
otherwise the first of their roles that does.
"""

from dataclasses import dataclass
from typing import Callable

from django.core.exceptions import ObjectDoesNotExist, ValidationError
from django.urls import NoReverseMatch, reverse

from accounts import roles
from accounts.models import User
from applicants.services import RECORD_KINDS
from committee.models import CommitteeConflict, Interview, InterviewEvaluation
from core.models import Credential
from matrix.models import ApplicantRoster, RosterEntry, RosterTemplate
from recruitment.models import Application, Position, Vacancy


@dataclass(frozen=True)
class Page:
    label: object                       # text, or function(record, kwargs)
    parent: str = None                  # URL name, or None for Home
    load: Callable = None               # function(kwargs) returning this page's record
    parent_kwargs: Callable = None      # function(record, kwargs) for the parent's URL


def _vacancy(kwargs):
    return Vacancy.objects.get(pk=kwargs['pk'])


def _application(kwargs):
    return Application.objects.select_related('vacancy').get(pk=kwargs['pk'])


def _position(kwargs):
    return Position.objects.get(pk=kwargs['pk'])


def _roster(kwargs):
    return ApplicantRoster.objects.get(pk=kwargs['pk'])


def _interview(kwargs):
    return Interview.objects.get(pk=kwargs['pk'])


def _evaluation(kwargs):
    return InterviewEvaluation.objects.get(pk=kwargs['pk'])


def _extraction_item(kwargs):
    from ai_processing.models import ExtractionItem
    return ExtractionItem.objects.select_related('extraction').get(
        pk=kwargs['item_pk'], extraction__job__application_id=kwargs['pk'])


def _same_pk(record, kwargs):
    return {'pk': kwargs['pk']}


def _application_pk(record, kwargs):
    return {'pk': record.application_id}


def _on_vacancy(label, **extra):
    return Page(label, 'recruitment:vacancy_detail', parent_kwargs=_same_pk, **extra)


def _on_position(label):
    return Page(label, 'recruitment:position_detail', parent_kwargs=_same_pk)


def _on_review(label):
    return Page(label, 'recruitment:application_review', parent_kwargs=_same_pk)


def _on_selection(label):
    return Page(label, 'recruitment:application_selection', parent_kwargs=_same_pk)


def _on_roster(label):
    return Page(label, 'matrix:roster_detail', parent_kwargs=_same_pk)


def _record_label(record, kwargs):
    action = 'Edit' if 'pk' in kwargs else 'Add'
    return f'{action} {RECORD_KINDS[kwargs["kind"]][1].lower()}'


PAGES = {
    # Account
    'accounts:account': Page('My account'),
    'accounts:password_change': Page('Change password', 'accounts:account'),
    'accounts:two_factor_recovery_codes': Page('New recovery codes', 'accounts:account'),
    'accounts:privacy': Page('Privacy notice'),
    'accounts:user_list': Page('System Admin'),
    'accounts:staff_create': Page('New staff account', 'accounts:user_list'),
    'accounts:user_access': Page(lambda user, kw: user.username, 'accounts:user_list',
                                 load=lambda kw: User.objects.get(pk=kw['pk'])),
    'notifications:list': Page('Notifications'),
    'notifications:deliveries': Page('Email deliveries'),

    # Staff workspaces
    'core:workspace_hr': Page('HR'),
    'core:workspace_approver': Page('Approver'),
    'core:workspace_department': Page('Department Head'),
    'core:workspace_psb': Page('PSB'),

    # Applicant
    'recruitment:public_list': Page('Vacancies'),
    'recruitment:public_detail': Page(lambda v, kw: v.position_title, 'recruitment:public_list',
                                      load=_vacancy),
    'applicants:profile': Page('My profile'),
    'applicants:edit_personal': Page('Personal information', 'applicants:profile'),
    'applicants:add_record': Page(_record_label, 'applicants:profile'),
    'applicants:edit_record': Page(_record_label, 'applicants:profile'),
    'recruitment:my_applications': Page('My applications'),
    'recruitment:my_application': Page(lambda a, kw: a.reference, 'recruitment:my_applications',
                                       load=_application),
    'recruitment:withdraw': Page('Withdraw', 'recruitment:my_application',
                                 parent_kwargs=_same_pk),

    # Vacancies and position catalog
    'recruitment:vacancy_list': Page('Vacancies'),
    'recruitment:vacancy_create': Page('New vacancy', 'recruitment:vacancy_list'),
    'recruitment:vacancy_detail': Page(lambda v, kw: v.position_title,
                                       'recruitment:vacancy_list', load=_vacancy),
    'recruitment:vacancy_edit': _on_vacancy('Edit details'),
    'recruitment:vacancy_apply_position': _on_vacancy('Load position defaults'),
    'recruitment:requirement_add': _on_vacancy('Add requirement'),
    'recruitment:version_detail': _on_vacancy(lambda r, kw: f'Requirements v{kw["version"]}'),
    'recruitment:position_list': Page('Position catalog'),
    'recruitment:position_create': Page('New position', 'recruitment:position_list'),
    'recruitment:position_detail': Page(lambda p, kw: p.title, 'recruitment:position_list',
                                        load=_position),
    'recruitment:position_edit': _on_position('Edit position'),
    'recruitment:position_item_add': _on_position('Add default requirement'),

    # Applications and decisions
    'recruitment:vacancy_applications': _on_vacancy('Applications'),
    'recruitment:verification_queue': Page('Document verification'),
    'recruitment:application_search': Page('Search applications'),
    'recruitment:decisions_pending': Page('Decisions awaiting release'),
    'recruitment:application_review': Page(
        lambda a, kw: a.reference, 'recruitment:vacancy_applications', load=_application,
        parent_kwargs=lambda a, kw: {'pk': a.vacancy_id}),
    'recruitment:verify_document': _on_review('Verify document'),
    'recruitment:request_correction': _on_review('Request correction'),
    'recruitment:correction_exception': _on_review('Late correction exception'),
    'recruitment:fact_add': _on_review('Add fact'),
    'recruitment:fact_correct': _on_review('Correct fact'),
    'recruitment:qualification_outcome': _on_review('Qualification outcome'),
    'recruitment:application_selection': _on_review('Selection'),
    'ai_processing:results': Page('AI extraction results'),
    'assistant:unanswered': Page('Help assistant questions'),
    'core:credential_list': Page('Licenses and eligibilities'),
    'core:credential_edit': Page(lambda c, kw: c.name, 'core:credential_list',
                                 load=lambda kw: Credential.objects.get(pk=kw['pk'])),
    'ai_processing:extraction': _on_review('AI extraction'),
    'ai_processing:screening': _on_review('Qualification check'),
    'ai_processing:matches': _on_review('Competency matching'),
    'ai_processing:item_edit': Page(
        'Edit extracted item', 'ai_processing:extraction', load=_extraction_item,
        parent_kwargs=lambda item, kw: {'pk': kw['pk'], 'job_pk': item.extraction.job_id}),
    'recruitment:decision_new': _on_selection('Record decision'),
    'recruitment:decision_correct': _on_selection('Correct decision'),
    'recruitment:decision_release': _on_selection('Release decision'),

    # Official roster
    'matrix:vacancy_rosters': _on_vacancy('Official roster'),
    'matrix:roster_detail': Page(
        lambda r, kw: f'Version {r.version}', 'matrix:vacancy_rosters', load=_roster,
        parent_kwargs=lambda r, kw: {'pk': r.vacancy_id}),
    'matrix:entry_edit': Page(
        lambda e, kw: f'Row {e.sequence_number}', 'matrix:roster_detail',
        load=lambda kw: RosterEntry.objects.get(pk=kw['entry_pk'], roster_id=kw['pk']),
        parent_kwargs=_same_pk),
    'matrix:signatories': _on_roster('Signatories'),
    'matrix:pending': Page('Rosters awaiting approval'),
    'matrix:templates': Page('Roster templates'),
    'matrix:template_signatories': Page(
        lambda t, kw: f'{t.name} signatories', 'matrix:templates',
        load=lambda kw: RosterTemplate.objects.get(pk=kw['pk'])),

    # Committees and interviews
    'committee:vacancy_committees': _on_vacancy('Committees and interviews'),
    'committee:conflict_resolve': Page(
        'Resolve conflict', 'committee:vacancy_committees',
        load=lambda kw: CommitteeConflict.objects.select_related('application')
        .get(pk=kw['pk']),
        parent_kwargs=lambda c, kw: {'pk': c.application.vacancy_id}),
    'committee:interview_new': Page('Schedule interview', 'recruitment:application_selection',
                                    parent_kwargs=lambda r, kw: {'pk': kw['app_pk']}),
    'committee:interview_reschedule': Page('Reschedule interview',
                                           'recruitment:application_selection', load=_interview,
                                           parent_kwargs=_application_pk),
    'committee:interview_attendance': Page('Record attendance',
                                           'recruitment:application_selection', load=_interview,
                                           parent_kwargs=_application_pk),
    'committee:my_candidates': Page('Assigned candidates'),
    'committee:candidate': Page(lambda a, kw: a.reference, 'committee:my_candidates',
                                load=_application),
    'committee:evaluation_new': Page('New evaluation', 'committee:candidate',
                                     parent_kwargs=_same_pk),
    'committee:evaluation_edit': Page('Evaluation', 'committee:candidate', load=_evaluation,
                                      parent_kwargs=_application_pk),
    'committee:evaluation_amend': Page('Amend evaluation', 'committee:candidate',
                                       load=_evaluation, parent_kwargs=_application_pk),

    # Reporting and operations
    'analytics:dashboard': Page('Analytics'),
    'analytics:snapshots': Page('Snapshots', 'analytics:dashboard'),
    'analytics:reports': Page('Reports', 'analytics:dashboard'),
    'audit:list': Page('Audit trail'),
    'operations:overview': Page('Backups and restore checks'),
}


# Pages that are "Home" for the person viewing them.
HOME_PAGES = {'core:home', 'core:dashboard', 'core:workspace_applicant'}

_RECRUITMENT = (roles.HR, roles.APPROVER, roles.DEPARTMENT_HEAD)

# Top-level staff pages and the workspaces they belong to, in order of preference.
WORKSPACE_ROLES = {
    'recruitment:vacancy_list': _RECRUITMENT,
    'recruitment:application_search': _RECRUITMENT,
    'analytics:dashboard': _RECRUITMENT,
    'recruitment:position_list': (roles.HR,),
    'recruitment:verification_queue': (roles.HR,),
    'ai_processing:results': (roles.HR,),
    'recruitment:decisions_pending': (roles.HR, roles.APPROVER),
    'matrix:pending': (roles.APPROVER,),
    'matrix:templates': (roles.APPROVER, roles.SYSTEM_ADMIN),
    'audit:list': (roles.APPROVER, roles.SYSTEM_ADMIN),
    'committee:my_candidates': (roles.DEPARTMENT_HEAD, roles.PSB_MEMBER),
    'notifications:deliveries': (roles.SYSTEM_ADMIN,),
    'operations:overview': (roles.SYSTEM_ADMIN,),
}


def _workspace_crumb(user, root, active_role):
    allowed = [r for r in WORKSPACE_ROLES.get(root, ()) if roles.has_role(user, r)]
    if not allowed:
        return None
    role = active_role if active_role in allowed else allowed[0]
    url_name, label = next((url, text) for r, url, text in roles.WORKSPACES if r == role)
    return {'label': label, 'url': reverse(url_name)}


def build_trail(user, view_name, kwargs, active_role=None):
    """[{'label', 'url'}, ...] from Home to the current page (url None), or []."""
    if view_name in HOME_PAGES:
        return [{'label': 'Home', 'url': None}]
    if view_name not in PAGES:
        return []
    trail = []
    root = view_name
    name, current = view_name, dict(kwargs)
    is_current = True
    try:
        while name:
            page = PAGES[name]
            root = name
            record = page.load(current) if page.load else None
            label = page.label(record, current) if callable(page.label) else page.label
            trail.append({'label': label, 'url': None if is_current
                          else reverse(name, kwargs=current)})
            is_current = False
            name, current = page.parent, (page.parent_kwargs(record, current)
                                          if page.parent_kwargs else {})
    except (ObjectDoesNotExist, ValidationError, NoReverseMatch, KeyError, ValueError):
        return []
    workspace = _workspace_crumb(user, root, active_role)
    if workspace:
        trail.append(workspace)
    home = 'core:dashboard' if user.is_authenticated else 'core:home'
    trail.append({'label': 'Home', 'url': reverse(home)})
    trail.reverse()
    return trail
