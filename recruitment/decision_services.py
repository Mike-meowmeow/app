"""Human recruitment decisions (FR-019, FR-013).

Shortlisting, committee recommendation, final outcome, and appointment are
separate recorded events. Recording is internal: the resulting status change
is logged as internal history, and the applicant sees a decision only after an
authorized release. Hired requires a recorded appointment (reference and
date) that follows a final outcome of Selected. Corrections add a record that
supersedes the previous one. No decision is ever inferred from roster order,
AI output, or exports.
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import roles
from audit import services as audit
from notifications import events

from . import application_services as applications
from . import selectors
from .models import (ApplicationStatus, DecisionOutcome, DecisionType, RecruitmentDecision,
                     Vacancy, VacancyStatus, WorkflowVisibility)

S = ApplicationStatus
T = DecisionType
O = DecisionOutcome

# (type, outcome) -> (statuses it may be recorded from, resulting status or None)
RULES = {
    (T.SHORTLISTING, O.SHORTLISTED): ({S.UNDER_REVIEW}, S.SHORTLISTED),
    (T.SHORTLISTING, O.NOT_SHORTLISTED): ({S.UNDER_REVIEW}, S.NOT_SELECTED),
    (T.COMMITTEE_RECOMMENDATION, O.RECOMMENDED): ({S.SHORTLISTED, S.FOR_INTERVIEW},
                                                  S.RECOMMENDED),
    (T.COMMITTEE_RECOMMENDATION, O.NOT_RECOMMENDED): ({S.SHORTLISTED, S.FOR_INTERVIEW}, None),
    (T.FINAL_OUTCOME, O.SELECTED): ({S.RECOMMENDED}, None),
    (T.FINAL_OUTCOME, O.NOT_SELECTED): ({S.SHORTLISTED, S.FOR_INTERVIEW, S.RECOMMENDED},
                                        S.NOT_SELECTED),
    (T.FINAL_OUTCOME, O.CLOSED_WITHOUT_SELECTION): (
        {S.UNDER_REVIEW, S.SHORTLISTED, S.FOR_INTERVIEW, S.RECOMMENDED},
        S.CLOSED_WITHOUT_SELECTION),
    (T.APPOINTMENT, O.APPOINTED): ({S.RECOMMENDED}, S.HIRED),
}
OUTCOMES = {t: [o for (tt, o) in RULES if tt == t] for t in T.values}

# Status the applicant sees once a decision is released.
RELEASED_STATUS = {
    (T.SHORTLISTING, O.SHORTLISTED): S.SHORTLISTED,
    (T.SHORTLISTING, O.NOT_SHORTLISTED): S.NOT_SELECTED,
    (T.FINAL_OUTCOME, O.NOT_SELECTED): S.NOT_SELECTED,
    (T.FINAL_OUTCOME, O.CLOSED_WITHOUT_SELECTION): S.CLOSED_WITHOUT_SELECTION,
    (T.APPOINTMENT, O.APPOINTED): S.HIRED,
}
RELEASABLE = (T.SHORTLISTING, T.FINAL_OUTCOME, T.APPOINTMENT)  # recommendations stay internal


def _authorized(user, application, role_names):
    if not role_names:
        return False
    vacancy = application.vacancy
    if roles.HR in role_names and selectors.can_manage(user, vacancy):
        return True
    others = [r for r in role_names if r != roles.HR]
    return bool(others) and roles.has_role(user, *others) and selectors.can_view(user, vacancy)


def can_record(user, application, decision_type):
    return _authorized(user, application, settings.DECISION_RECORDING_ROLES.get(decision_type))


def can_release(user, decision):
    return decision.decision_type in RELEASABLE and _authorized(
        user, decision.application, settings.DECISION_RELEASE_ROLES.get(decision.decision_type))


def current(application, decision_type):
    return application.decisions.filter(decision_type=decision_type,
                                        superseded_by__isnull=True).first()


def recordable_types(user, application):
    """Decision types the user may record now, with their allowed outcomes."""
    result = []
    for decision_type in T.values:
        if not can_record(user, application, decision_type):
            continue
        outcomes = [o for o in OUTCOMES[decision_type]
                    if application.status in RULES[(decision_type, o)][0]]
        if outcomes:
            result.append((T(decision_type), [O(o) for o in outcomes]))
    return result


def _references(text, extra=None):
    lines = [line.strip() for line in (text or '').splitlines() if line.strip()]
    return {'schema_version': 1, 'references': lines, **(extra or {})}


def _check_decision_at(decision_at):
    decision_at = decision_at or timezone.now()
    if decision_at > timezone.now():
        raise ValidationError('The decision date cannot be in the future.')
    return decision_at


def _hired_count(vacancy):
    return vacancy.applications.filter(status=S.HIRED).count()


@transaction.atomic
def record_decision(actor, application, *, decision_type, outcome, reason, decision_at=None,
                    committee=None, references='', appointment_reference='',
                    appointment_at=None):
    # Vacancy lock first, as in submission and roster approval.
    vacancy = Vacancy.objects.select_for_update().get(pk=application.vacancy_id)
    application = applications._lock(application)
    if not can_record(actor, application, decision_type):
        raise PermissionDenied('You are not authorized to record this decision.')
    rule = RULES.get((decision_type, outcome))
    if rule is None:
        raise ValidationError('Choose a valid outcome for this decision.')
    allowed, new_status = rule
    if application.status not in allowed:
        raise ValidationError(f'This decision cannot be recorded while the application is '
                              f'{S(application.status).label.lower()}.')
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('Record the justification for this decision.')
    decision_at = _check_decision_at(decision_at)
    if committee is not None and committee.vacancy_id != vacancy.pk:
        raise ValidationError('The committee belongs to another vacancy.')
    extra = {}
    if decision_type == T.COMMITTEE_RECOMMENDATION:
        if committee is None:
            raise ValidationError('Choose the committee making the recommendation.')
        from committee import services as committee_services
        extra['evaluation_ids'] = [str(e.pk) for e in
                                   committee_services.current_submitted(application, committee)]
    if decision_type == T.APPOINTMENT:
        appointment_reference = (appointment_reference or '').strip()
        if not appointment_reference or appointment_at is None:
            raise ValidationError('An appointment needs its reference and date.')
        final = current(application, T.FINAL_OUTCOME)
        if final is None or final.outcome != O.SELECTED:
            raise ValidationError('Record a final outcome of Selected before the appointment.')
        if _hired_count(vacancy) >= vacancy.positions_available:
            raise ValidationError(f'All {vacancy.positions_available} position(s) for this '
                                  'vacancy are already filled.')
    else:
        appointment_reference, appointment_at = '', None

    previous = current(application, decision_type)
    decision = RecruitmentDecision.objects.create(
        application=application, committee=committee, decision_type=decision_type,
        outcome=outcome, reason=reason, supporting_references=_references(references, extra),
        decided_by=actor, decision_at=decision_at, supersedes=previous,
        appointment_reference=appointment_reference, appointment_at=appointment_at)
    old_status = application.status
    if new_status:
        applications.transition(application, new_status, actor=actor,
                                reason=f'{T(decision_type).label}: {O(outcome).label}',
                                visibility=WorkflowVisibility.INTERNAL)
        if {old_status, new_status} & applications.CONCLUDED:
            applications._bump_revision(application)  # roster remarks change
    audit.record('decision.recorded', entity=decision, actor=actor, reason=reason,
                 changes={'application': application.reference, 'decision_type': decision_type,
                          'outcome': outcome, 'supersedes': str(previous.pk) if previous else None,
                          'status': {'old': old_status, 'new': application.status}})
    if decision_type == T.APPOINTMENT and _hired_count(vacancy) >= vacancy.positions_available \
            and vacancy.status in (VacancyStatus.PUBLISHED, VacancyStatus.CLOSED):
        old = vacancy.status
        vacancy.status = VacancyStatus.FILLED
        vacancy.filled_at = timezone.now()
        vacancy.save(update_fields=['status', 'filled_at'])
        audit.record('vacancy.filled', entity=vacancy, actor=actor,
                     changes={'status': {'old': old, 'new': VacancyStatus.FILLED},
                              'appointment': decision.appointment_reference})
    return decision


@transaction.atomic
def correct_decision(actor, decision, *, correction_reason, reason, decision_at=None,
                     references='', appointment_reference='', appointment_at=None):
    """Correct the record of a decision without changing its outcome: a new
    record supersedes the old one, which stays in history. To change an
    outcome, reopen the application and record a new decision."""
    applications._lock(decision.application)
    decision = RecruitmentDecision.objects.select_for_update().select_related(
        'application__vacancy').get(pk=decision.pk)
    application = decision.application
    if not can_record(actor, application, decision.decision_type):
        raise PermissionDenied('You are not authorized to correct this decision.')
    if not decision.is_current:
        raise ValidationError('This decision was already superseded.')
    correction_reason = (correction_reason or '').strip()
    reason = (reason or '').strip()
    if not correction_reason or not reason:
        raise ValidationError('Give the justification and the reason for the correction.')
    decision_at = _check_decision_at(decision_at or decision.decision_at)
    if decision.decision_type == T.APPOINTMENT:
        appointment_reference = (appointment_reference or '').strip()
        if not appointment_reference or appointment_at is None:
            raise ValidationError('An appointment needs its reference and date.')
    else:
        appointment_reference, appointment_at = '', None
    extra = {k: v for k, v in decision.supporting_references.items()
             if k not in ('schema_version', 'references', 'correction_reason')}
    extra['correction_reason'] = correction_reason
    new = RecruitmentDecision.objects.create(
        application=application, committee=decision.committee,
        decision_type=decision.decision_type, outcome=decision.outcome, reason=reason,
        supporting_references=_references(references, extra), decided_by=actor,
        decision_at=decision_at, supersedes=decision,
        appointment_reference=appointment_reference, appointment_at=appointment_at)
    audit.record('decision.corrected', entity=new, actor=actor, reason=correction_reason,
                 changes=audit.diff(audit.snapshot(decision, CORRECTABLE),
                                    audit.snapshot(new, CORRECTABLE)))
    return new


CORRECTABLE = ['reason', 'supporting_references', 'decision_at', 'appointment_reference',
               'appointment_at']


@transaction.atomic
def release_decision(actor, decision, *, note=''):
    """Applicant-facing release, separate from internal recording."""
    decision = RecruitmentDecision.objects.select_for_update().select_related(
        'application__vacancy').get(pk=decision.pk)
    if decision.decision_type not in RELEASABLE:
        raise ValidationError('Committee recommendations are internal and are not released.')
    if not can_release(actor, decision):
        raise PermissionDenied('You are not authorized to release this decision.')
    if not decision.is_current:
        raise ValidationError('This decision was superseded; release the current record.')
    if decision.is_released:
        raise ValidationError('This decision was already released.')
    decision.released_by = actor
    decision.released_at = timezone.now()
    decision.release_note = (note or '').strip()
    decision.save(update_fields=['released_by', 'released_at', 'release_note'])
    audit.record('decision.released', entity=decision, actor=actor,
                 changes={'application': decision.application.reference,
                          'decision_type': decision.decision_type, 'outcome': decision.outcome})
    events.decision_released(decision)
    return decision


def pending_release(user):
    """Current, unreleased, releasable decisions the user may release."""
    qs = RecruitmentDecision.objects.filter(
        decision_type__in=RELEASABLE, released_at__isnull=True, superseded_by__isnull=True,
        application__vacancy__in=selectors.visible_vacancies(user).values('pk'),
    ).select_related('application__vacancy', 'application__applicant__user', 'decided_by') \
        .order_by('decision_at')
    return [d for d in qs if can_release(user, d)]


# --------------------------------------------------------------------------
# What the applicant sees
# --------------------------------------------------------------------------

def released_decisions(application):
    """Latest released decision per type, in release order."""
    latest = {}
    for d in application.decisions.filter(released_at__isnull=False).order_by('released_at'):
        latest[d.decision_type] = d
    return sorted(latest.values(), key=lambda d: d.released_at)


def applicant_status(application):
    """The status the applicant may see: the latest applicant-visible workflow
    change or released decision. Internal decision changes stay hidden."""
    log = application.workflow_logs.filter(visibility=WorkflowVisibility.APPLICANT) \
        .order_by('-occurred_at', '-created_at').first()
    when, status = (log.occurred_at, log.to_status) if log else (None, application.status)
    for d in application.decisions.filter(released_at__isnull=False):
        released = RELEASED_STATUS.get((d.decision_type, d.outcome))
        if released and (when is None or d.released_at > when):
            when, status = d.released_at, released
    return status


class PublicStatus:
    """Stand-in for templates that show `app.status` / `app.get_status_display`."""

    def __init__(self, status):
        self.status = status

    def get_status_display(self):
        return S(self.status).label
