"""Committee, conflict, interview, and evaluation operations (FR-010, FR-011).

Every operation authorizes the actor, runs in one transaction, and records an
audit event. Conflicts block participation; schedule changes keep the old
schedule; submitted evaluations are amended by new versions, never edited.
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from accounts import roles
from audit import services as audit
from notifications import events
from recruitment import application_services as applications
from recruitment.models import (Application, ApplicationStatus, DecisionOutcome, DecisionType,
                                VacancyStatus, WorkflowVisibility)

from . import selectors
from .models import (AttendanceStatus, Committee, CommitteeConflict, CommitteeMember,
                     ConflictStatus, EvaluationStatus, Interview, InterviewEvaluation,
                     InterviewEvent, InterviewEventType, InterviewMode, InterviewPanelMember,
                     InterviewStatus, MemberRole)

S = ApplicationStatus
INTERVIEW_STATUSES = (S.SHORTLISTED, S.FOR_INTERVIEW)
COMMITTEE_VACANCY_STATUSES = (VacancyStatus.PUBLISHED, VacancyStatus.CLOSED)


def _require_manage(actor, vacancy):
    if not selectors.can_manage(actor, vacancy):
        raise PermissionDenied('You are not assigned to manage this vacancy.')


def _name(user):
    return user.full_name or user.username


# --------------------------------------------------------------------------
# Committees and membership
# --------------------------------------------------------------------------

@transaction.atomic
def create_committee(actor, vacancy, *, name):
    _require_manage(actor, vacancy)
    name = (name or '').strip()
    if not name:
        raise ValidationError('Enter a committee name.')
    if vacancy.status not in COMMITTEE_VACANCY_STATUSES:
        raise ValidationError('Committees can be created for published or closed vacancies.')
    committee = Committee.objects.create(vacancy=vacancy, name=name)
    audit.record('committee.created', entity=committee, actor=actor,
                 changes={'vacancy': vacancy.reference, 'name': name})
    return committee


@transaction.atomic
def set_committee_active(actor, committee, *, active, reason):
    _require_manage(actor, committee.vacancy)
    if not reason:
        raise ValidationError('A reason is required.')
    if committee.is_active == active:
        return committee
    committee.is_active = active
    committee.save(update_fields=['is_active'])
    audit.record('committee.activated' if active else 'committee.deactivated',
                 entity=committee, actor=actor, reason=reason)
    return committee


def eligible_member_users(committee):
    from accounts.models import User
    return User.objects.filter(is_active=True, groups__name__in=settings.COMMITTEE_MEMBER_ROLES) \
        .exclude(committee_memberships__committee=committee,
                 committee_memberships__ended_at__isnull=True) \
        .distinct().order_by('last_name', 'first_name', 'username')


@transaction.atomic
def add_member(actor, committee, user, *, member_role=MemberRole.MEMBER):
    _require_manage(actor, committee.vacancy)
    if not committee.is_active:
        raise ValidationError('This committee is inactive.')
    if not user.is_active or not roles.has_role(user, *settings.COMMITTEE_MEMBER_ROLES):
        raise ValidationError('Committee members must be active PSB Members or Department '
                              'Heads.')
    if Application.objects.filter(vacancy=committee.vacancy, applicant__user=user).exists():
        raise ValidationError('An applicant to this vacancy cannot sit on its committee.')
    if member_role == MemberRole.CHAIR and committee.members.filter(
            member_role=MemberRole.CHAIR, ended_at__isnull=True).exclude(user=user).exists():
        raise ValidationError('This committee already has a chair.')
    member = CommitteeMember.objects.select_for_update().filter(committee=committee,
                                                                user=user).first()
    if member and member.is_active:
        raise ValidationError(f'{_name(user)} is already a member.')
    if member:  # reactivate the existing membership (unique committee/user)
        member.ended_at = None
        member.member_role = member_role
        member.save(update_fields=['ended_at', 'member_role'])
        action = 'committee.member_reactivated'
    else:
        member = CommitteeMember.objects.create(committee=committee, user=user,
                                                member_role=member_role)
        action = 'committee.member_added'
    audit.record(action, entity=member, actor=actor,
                 changes={'committee': committee.name, 'user': user.username,
                          'member_role': member_role})
    events.committee_member_added(member)
    return member


@transaction.atomic
def end_member(actor, member, *, reason):
    _require_manage(actor, member.committee.vacancy)
    if not reason:
        raise ValidationError('A reason is required to end a membership.')
    member = CommitteeMember.objects.select_for_update().get(pk=member.pk)
    if not member.is_active:
        raise ValidationError('This membership has already ended.')
    member.ended_at = timezone.now()
    member.save(update_fields=['ended_at'])
    audit.record('committee.member_ended', entity=member, actor=actor, reason=reason,
                 changes={'committee': member.committee.name, 'user': member.user.username})
    return member


# --------------------------------------------------------------------------
# Conflicts of interest
# --------------------------------------------------------------------------

@transaction.atomic
def declare_conflict(actor, member, application, *, declaration):
    """Declared by the member, or recorded for them by the vacancy's HR."""
    if member.user_id != actor.pk:
        _require_manage(actor, application.vacancy)
    if not member.is_active or member.committee.vacancy_id != application.vacancy_id:
        raise ValidationError('The member is not on a committee for this vacancy.')
    if application.status == S.DRAFT:
        raise ValidationError('Conflicts are declared for submitted applications.')
    declaration = (declaration or '').strip()
    if not declaration:
        raise ValidationError('Describe the conflict of interest.')
    if selectors.has_blocking_conflict(member, application):
        raise ValidationError('A conflict is already recorded for this applicant.')
    conflict = CommitteeConflict.objects.create(committee_member=member, application=application,
                                                declaration=declaration)
    audit.record('conflict.declared', entity=conflict, actor=actor,
                 changes={'application': application.reference, 'member': member.user.username})
    events.conflict_declared(conflict)
    return conflict


def can_resolve_conflicts(user, vacancy):
    return roles.has_role(user, *settings.CONFLICT_RESOLUTION_ROLES) and \
        selectors.can_oversee(user, vacancy)


@transaction.atomic
def resolve_conflict(actor, conflict, *, status, resolution):
    conflict = CommitteeConflict.objects.select_for_update().select_related(
        'committee_member__user', 'application__vacancy').get(pk=conflict.pk)
    if not can_resolve_conflicts(actor, conflict.application.vacancy):
        raise PermissionDenied('You are not authorized to resolve conflicts of interest.')
    if conflict.committee_member.user_id == actor.pk:
        raise PermissionDenied('A member cannot resolve their own conflict.')
    if conflict.status != ConflictStatus.OPEN:
        raise ValidationError('This conflict has already been resolved.')
    if status not in (ConflictStatus.RECUSED, ConflictStatus.CLEARED):
        raise ValidationError('Choose recused or cleared.')
    resolution = (resolution or '').strip()
    if not resolution:
        raise ValidationError('Explain the resolution.')
    conflict.status = status
    conflict.resolution = resolution
    conflict.resolved_by = actor
    conflict.resolved_at = timezone.now()
    conflict.save(update_fields=['status', 'resolution', 'resolved_by', 'resolved_at'])
    audit.record('conflict.resolved', entity=conflict, actor=actor, reason=resolution,
                 changes={'status': {'old': ConflictStatus.OPEN, 'new': status}})
    return conflict


# --------------------------------------------------------------------------
# Interviews (FR-011)
# --------------------------------------------------------------------------

def _clean_schedule(*, starts_at, ends_at, mode, location='', meeting_link=''):
    problems = []
    if not starts_at or not ends_at:
        problems.append('Enter the start and end time.')
    elif ends_at <= starts_at:
        problems.append('The interview must end after it starts.')
    elif starts_at <= timezone.now():
        problems.append('The interview must start in the future.')
    location, meeting_link = (location or '').strip(), (meeting_link or '').strip()
    if mode == InterviewMode.IN_PERSON and not location:
        problems.append('Enter the venue for an in-person interview.')
    elif mode == InterviewMode.ONLINE and not meeting_link:
        problems.append('Enter the meeting link for an online interview.')
    elif mode not in InterviewMode.values:
        problems.append('Choose the interview mode.')
    if meeting_link and not meeting_link.lower().startswith(('https://', 'http://')):
        problems.append('The meeting link must start with https://.')
    if problems:
        raise ValidationError(problems)
    return {'starts_at': starts_at, 'ends_at': ends_at, 'mode': mode, 'location': location,
            'meeting_link': meeting_link}


def _check_panel_member(member, application):
    if not member.is_active or not member.committee.is_active \
            or member.committee.vacancy_id != application.vacancy_id:
        raise ValidationError(f'{_name(member.user)} is not an active member of a committee '
                              'for this vacancy.')
    if selectors.has_blocking_conflict(member, application):
        raise ValidationError(f'{_name(member.user)} has a conflict of interest with this '
                              'applicant and cannot sit on the panel.')


def panel_candidates(application):
    """Active members of the vacancy's active committees without a blocking conflict."""
    return [m for m in CommitteeMember.objects.filter(
                committee__vacancy_id=application.vacancy_id, committee__is_active=True,
                ended_at__isnull=True).select_related('user', 'committee')
            if not selectors.has_blocking_conflict(m, application)]


def shortlisting_released(application):
    decision = application.decisions.filter(
        decision_type=DecisionType.SHORTLISTING, superseded_by__isnull=True).first()
    return bool(decision and decision.outcome == DecisionOutcome.SHORTLISTED
                and decision.is_released)


def scheduling_problems(application):
    problems = []
    if application.status not in INTERVIEW_STATUSES:
        problems.append('Only shortlisted applicants can be invited to an interview.')
    elif not shortlisting_released(application):
        problems.append('Release the shortlisting decision to the applicant before inviting '
                        'them to an interview.')
    if application.interviews.filter(status=InterviewStatus.SCHEDULED).exists():
        problems.append('This applicant already has a scheduled interview; reschedule or '
                        'complete it first.')
    return problems


def _event(interview, event_type, actor, *, old=None, new=None, reason=''):
    return InterviewEvent.objects.create(interview=interview, event_type=event_type,
                                         old_schedule=old, new_schedule=new, reason=reason,
                                         actor=actor)


@transaction.atomic
def schedule_interview(actor, application, *, panel, starts_at, ends_at, mode, location='',
                       meeting_link=''):
    application = applications._lock(application)
    _require_manage(actor, application.vacancy)
    problems = scheduling_problems(application)
    if problems:
        raise ValidationError(problems)
    data = _clean_schedule(starts_at=starts_at, ends_at=ends_at, mode=mode, location=location,
                           meeting_link=meeting_link)
    panel = list(panel)
    if not panel:
        raise ValidationError('Choose at least one panel member.')
    for member in panel:
        _check_panel_member(member, application)
    interview = Interview.objects.create(application=application, scheduled_by=actor, **data)
    for member in panel:
        InterviewPanelMember.objects.create(interview=interview, committee_member=member)
    events.interview_event(interview, _event(interview, InterviewEventType.SCHEDULED, actor,
                                             new=interview.schedule()))
    if application.status == S.SHORTLISTED:
        applications.transition(application, S.FOR_INTERVIEW, actor=actor,
                                reason='Invited to an interview',
                                visibility=WorkflowVisibility.APPLICANT)
    audit.record('interview.scheduled', entity=interview, actor=actor,
                 changes={'application': application.reference, **interview.schedule(),
                          'panel': [m.user.username for m in panel]})
    return interview


def _locked_interview(interview):
    return Interview.objects.select_for_update().select_related(
        'application__vacancy').get(pk=interview.pk)


@transaction.atomic
def reschedule_interview(actor, interview, *, reason, starts_at, ends_at, mode, location='',
                         meeting_link=''):
    interview = _locked_interview(interview)
    _require_manage(actor, interview.application.vacancy)
    if interview.status != InterviewStatus.SCHEDULED:
        raise ValidationError('Only scheduled interviews can be rescheduled.')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to reschedule.')
    data = _clean_schedule(starts_at=starts_at, ends_at=ends_at, mode=mode, location=location,
                           meeting_link=meeting_link)
    old = interview.schedule()
    for field, value in data.items():
        setattr(interview, field, value)
    if interview.schedule() == old:
        raise ValidationError('The schedule did not change.')
    interview.save(update_fields=list(data))
    events.interview_event(interview, _event(interview, InterviewEventType.RESCHEDULED, actor,
                                             old=old, new=interview.schedule(), reason=reason))
    audit.record('interview.rescheduled', entity=interview, actor=actor, reason=reason,
                 changes={'old': old, 'new': interview.schedule()})
    return interview


@transaction.atomic
def cancel_interview(actor, interview, *, reason):
    interview = _locked_interview(interview)
    _require_manage(actor, interview.application.vacancy)
    if interview.status != InterviewStatus.SCHEDULED:
        raise ValidationError('Only scheduled interviews can be canceled.')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to cancel an interview.')
    interview.status = InterviewStatus.CANCELED
    interview.attendance_status = AttendanceStatus.NOT_APPLICABLE
    interview.save(update_fields=['status', 'attendance_status'])
    events.interview_event(interview, _event(interview, InterviewEventType.CANCELED, actor,
                                             old=interview.schedule(), reason=reason))
    audit.record('interview.canceled', entity=interview, actor=actor, reason=reason)
    return interview


@transaction.atomic
def record_attendance(actor, interview, *, attendance, notes=''):
    interview = _locked_interview(interview)
    _require_manage(actor, interview.application.vacancy)
    if interview.status != InterviewStatus.SCHEDULED:
        raise ValidationError('Attendance is recorded once, for a scheduled interview.')
    if interview.starts_at > timezone.now():
        raise ValidationError('Attendance can be recorded after the interview starts.')
    if attendance not in (AttendanceStatus.ATTENDED, AttendanceStatus.NO_SHOW):
        raise ValidationError('Choose attended or no show.')
    interview.status = InterviewStatus.COMPLETED
    interview.attendance_status = attendance
    interview.save(update_fields=['status', 'attendance_status'])
    _event(interview, InterviewEventType.COMPLETED, actor, reason=notes or '')
    audit.record('interview.completed', entity=interview, actor=actor, reason=notes,
                 changes={'attendance_status': attendance})
    return interview


@transaction.atomic
def add_panel_member(actor, interview, member):
    interview = _locked_interview(interview)
    _require_manage(actor, interview.application.vacancy)
    if interview.status != InterviewStatus.SCHEDULED:
        raise ValidationError('The panel can only change before the interview is completed.')
    _check_panel_member(member, interview.application)
    if interview.panel.filter(committee_member=member).exists():
        raise ValidationError(f'{_name(member.user)} is already on the panel.')
    InterviewPanelMember.objects.create(interview=interview, committee_member=member)
    added = _event(interview, InterviewEventType.PANEL_ADDED, actor,
                   new={'member': str(member.pk), 'user': member.user.username})
    audit.record('interview.panel_added', entity=interview, actor=actor,
                 changes={'member': member.user.username})
    events.panel_member_added(interview, member, added)


@transaction.atomic
def remove_panel_member(actor, interview, member, *, reason):
    interview = _locked_interview(interview)
    _require_manage(actor, interview.application.vacancy)
    if interview.status != InterviewStatus.SCHEDULED:
        raise ValidationError('The panel can only change before the interview is completed.')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to remove a panel member.')
    seat = interview.panel.filter(committee_member=member).first()
    if seat is None:
        raise ValidationError('This member is not on the panel.')
    seat.delete()  # the event below keeps the history
    _event(interview, InterviewEventType.PANEL_REMOVED, actor,
           old={'member': str(member.pk), 'user': member.user.username}, reason=reason)
    audit.record('interview.panel_removed', entity=interview, actor=actor, reason=reason,
                 changes={'member': member.user.username})


def panel_conflicts(interview):
    """Panel seats whose member now has a blocking conflict (to be replaced)."""
    return [seat for seat in interview.panel.select_related('committee_member__user')
            if selectors.has_blocking_conflict(seat.committee_member, interview.application)]


# --------------------------------------------------------------------------
# Evaluations (FR-010)
# --------------------------------------------------------------------------

def _require_assessor(actor, application):
    """The actor's active membership, if they may assess this application."""
    member = selectors.membership_for(actor, application)
    if member is None:
        raise PermissionDenied('You are not on a committee for this vacancy.')
    if selectors.has_blocking_conflict(member, application):
        raise PermissionDenied('You declared a conflict of interest with this applicant.')
    if not selectors.can_view_candidate(actor, application):
        raise PermissionDenied('This applicant is not assigned to your committee.')
    return member


def _require_author(actor, evaluation):
    if evaluation.committee_member.user_id != actor.pk:
        raise PermissionDenied('Only the author can change this evaluation.')
    if not evaluation.committee_member.is_active:
        raise PermissionDenied('Your committee membership for this evaluation has ended.')
    _require_assessor(actor, evaluation.application)


@transaction.atomic
def start_evaluation(actor, application, *, findings, recommendation='', interview=None):
    application = applications._lock(application)
    member = _require_assessor(actor, application)
    if interview is not None:
        interview = Interview.objects.get(pk=interview.pk)  # current status, not a stale copy
        if interview.application_id != application.pk:
            raise ValidationError('The interview belongs to another application.')
        if not interview.panel.filter(committee_member=member).exists():
            raise PermissionDenied('You were not on this interview panel.')
        if interview.status != InterviewStatus.COMPLETED or \
                interview.attendance_status != AttendanceStatus.ATTENDED:
            raise ValidationError('Interview findings are recorded after the applicant '
                                  'attended the interview.')
    if InterviewEvaluation.objects.filter(application=application, committee_member=member,
                                          interview=interview).exists():
        raise ValidationError('You already have an evaluation for this; edit the draft or '
                              'amend the submitted version.')
    findings = (findings or '').strip()
    if not findings:
        raise ValidationError('Enter your findings.')
    evaluation = InterviewEvaluation.objects.create(
        application=application, interview=interview, committee_member=member, version=1,
        findings=findings, recommendation=(recommendation or '').strip())
    audit.record('evaluation.drafted', entity=evaluation, actor=actor,
                 changes={'application': application.reference, 'version': 1,
                          'interview': str(interview.pk) if interview else None})
    return evaluation


@transaction.atomic
def update_draft(actor, evaluation, *, findings, recommendation=''):
    evaluation = InterviewEvaluation.objects.select_for_update().get(pk=evaluation.pk)
    _require_author(actor, evaluation)
    if evaluation.status != EvaluationStatus.DRAFT:
        raise ValidationError('Submitted evaluations cannot be edited; amend them instead.')
    findings = (findings or '').strip()
    if not findings:
        raise ValidationError('Enter your findings.')
    evaluation.findings = findings
    evaluation.recommendation = (recommendation or '').strip()
    evaluation.save(update_fields=['findings', 'recommendation'])
    return evaluation


@transaction.atomic
def submit_evaluation(actor, evaluation):
    evaluation = InterviewEvaluation.objects.select_for_update().get(pk=evaluation.pk)
    _require_author(actor, evaluation)
    if evaluation.status != EvaluationStatus.DRAFT:
        raise ValidationError('This evaluation was already submitted.')
    evaluation.status = EvaluationStatus.SUBMITTED
    evaluation.submitted_at = timezone.now()
    evaluation.save(update_fields=['status', 'submitted_at'])
    audit.record('evaluation.submitted', entity=evaluation, actor=actor,
                 reason=evaluation.amendment_reason or None,
                 changes={'application': evaluation.application.reference,
                          'version': evaluation.version,
                          'supersedes': str(evaluation.supersedes_id or '') or None})
    return evaluation


@transaction.atomic
def amend_evaluation(actor, evaluation, *, findings, recommendation='', reason):
    """Start an amendment: a new draft version that supersedes the submitted one
    once submitted. The submitted version is never changed."""
    applications._lock(evaluation.application)
    evaluation = InterviewEvaluation.objects.select_for_update().get(pk=evaluation.pk)
    _require_author(actor, evaluation)
    if evaluation.status != EvaluationStatus.SUBMITTED:
        raise ValidationError('Only submitted evaluations are amended; edit the draft instead.')
    if InterviewEvaluation.objects.filter(supersedes=evaluation).exists():
        raise ValidationError('This version already has an amendment.')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A reason is required to amend a submitted evaluation.')
    findings = (findings or '').strip()
    if not findings:
        raise ValidationError('Enter your findings.')
    latest = InterviewEvaluation.objects.filter(
        application=evaluation.application, committee_member=evaluation.committee_member,
        interview=evaluation.interview).aggregate(v=Max('version'))['v']
    amendment = InterviewEvaluation.objects.create(
        application=evaluation.application, interview=evaluation.interview,
        committee_member=evaluation.committee_member, version=latest + 1,
        supersedes=evaluation, findings=findings,
        recommendation=(recommendation or '').strip(), amendment_reason=reason)
    audit.record('evaluation.amendment_started', entity=amendment, actor=actor, reason=reason,
                 changes={'version': amendment.version, 'supersedes': str(evaluation.pk)})
    return amendment


@transaction.atomic
def discard_draft(actor, evaluation):
    evaluation = InterviewEvaluation.objects.select_for_update().get(pk=evaluation.pk)
    _require_author(actor, evaluation)
    if evaluation.status != EvaluationStatus.DRAFT:
        raise ValidationError('Submitted evaluations cannot be discarded.')
    audit.record('evaluation.draft_discarded', entity=evaluation, actor=actor,
                 changes={'version': evaluation.version})
    evaluation.delete()


def current_submitted(application, committee=None):
    """Submitted evaluations not replaced by a submitted amendment."""
    qs = InterviewEvaluation.objects.filter(application=application,
                                            status=EvaluationStatus.SUBMITTED) \
        .select_related('committee_member__user', 'superseded_by')
    if committee is not None:
        qs = qs.filter(committee_member__committee=committee)
    return [e for e in qs if e.is_current]
