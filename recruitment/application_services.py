"""Application workflow (FR-006, FR-013, FR-025) and manual review of facts
and qualifications (FR-009 manual, FR-024 manual entry).

Every operation authorizes the actor, runs in one transaction, records a
workflow log and/or audit event, and increments the application revision
when roster-visible information changes.
"""

from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from accounts import roles
from applicants import services as applicant_services
from applicants.models import DEGREE_RANK
from audit import services as audit
from notifications import events

from . import application_selectors as access
from . import fact_schema
from .signals import application_changed
from .models import (Application, ApplicationCorrection, ApplicationFact, ApplicationSnapshot,
                     ApplicationStatus, CorrectionRequest, CorrectionStatus, FactReviewStatus,
                     FactType, QualificationStandard, RelevanceStatus, SnapshotType, Vacancy,
                     WorkflowLog, WorkflowVisibility)

S = ApplicationStatus

# Allowed status changes (FR-013). Stage 7 decision services
# (decision_services.py) record decisions through the same table; AI never
# calls transition().
TRANSITIONS = {
    S.DRAFT: {S.SUBMITTED},
    S.SUBMITTED: {S.UNDER_REVIEW, S.CORRECTION_REQUESTED, S.WITHDRAWN},
    S.UNDER_REVIEW: {S.CORRECTION_REQUESTED, S.SHORTLISTED, S.NOT_SELECTED, S.WITHDRAWN,
                     S.CLOSED_WITHOUT_SELECTION},
    S.CORRECTION_REQUESTED: {S.UNDER_REVIEW, S.WITHDRAWN},
    # Recommended directly after a document-only committee review.
    S.SHORTLISTED: {S.FOR_INTERVIEW, S.RECOMMENDED, S.NOT_SELECTED, S.WITHDRAWN,
                    S.CLOSED_WITHOUT_SELECTION},
    S.FOR_INTERVIEW: {S.RECOMMENDED, S.NOT_SELECTED, S.WITHDRAWN, S.CLOSED_WITHOUT_SELECTION},
    S.RECOMMENDED: {S.HIRED, S.NOT_SELECTED, S.WITHDRAWN, S.CLOSED_WITHOUT_SELECTION},
    S.WITHDRAWN: {S.UNDER_REVIEW},  # authorized reopening only
    S.HIRED: set(),
    S.NOT_SELECTED: {S.UNDER_REVIEW},  # authorized reopening only
    S.CLOSED_WITHOUT_SELECTION: {S.UNDER_REVIEW},  # authorized reopening only
}
WITHDRAWABLE = {S.SUBMITTED, S.UNDER_REVIEW, S.CORRECTION_REQUESTED, S.SHORTLISTED,
                S.FOR_INTERVIEW, S.RECOMMENDED}
CONCLUDED = {S.WITHDRAWN, S.NOT_SELECTED, S.CLOSED_WITHOUT_SELECTION}


def _bump_revision(application):
    Application.objects.filter(pk=application.pk).update(revision=F('revision') + 1)
    application.refresh_from_db(fields=['revision'])
    application_changed.send(sender=Application, application=application)


def _lock(application):
    return Application.objects.select_for_update().select_related(
        'vacancy', 'applicant__user').get(pk=application.pk)


def transition(application, to_status, *, actor, reason='',
               visibility=WorkflowVisibility.APPLICANT):
    """Change status with validation and history. Callers authorize."""
    from_status = application.status
    if to_status not in TRANSITIONS.get(from_status, set()):
        raise ValidationError(f'An application cannot move from '
                              f'{S(from_status).label} to {S(to_status).label}.')
    application.status = to_status
    application.save(update_fields=['status'])
    WorkflowLog.objects.create(application=application, from_status=from_status,
                               to_status=to_status, actor=actor, reason=reason,
                               visibility=visibility)
    audit.record('application.status_changed', entity=application, actor=actor, reason=reason,
                 changes={'status': {'old': from_status, 'new': to_status}})


def _require_owner(user, application):
    if not access.is_owner(user, application):
        raise PermissionDenied('This is not your application.')


def _require_reviewer(user, application):
    if not access.can_review(user, application):
        raise PermissionDenied('You are not assigned to review applications for this vacancy.')


def requirements_for(application):
    """The requirement version the application is held to: the one captured at
    submission, or the vacancy's current version while drafting."""
    snapshot = application.latest_snapshot()
    return snapshot.requirement_version if snapshot else application.vacancy.current_requirements()


# --------------------------------------------------------------------------
# Drafts and submission (FR-006)
# --------------------------------------------------------------------------

@transaction.atomic
def start_application(user, vacancy):
    """Return the applicant's application for this vacancy, creating a draft.
    One application per vacancy (also enforced by a unique constraint)."""
    if not roles.has_role(user, roles.APPLICANT):
        raise PermissionDenied('Only applicants can apply.')
    profile = applicant_services.get_profile(user)
    existing = Application.objects.filter(applicant=profile, vacancy=vacancy).first()
    if existing:
        return existing, False
    if not vacancy.is_accepting_applications():
        raise ValidationError('This vacancy is not accepting applications.')
    application = Application.objects.create(applicant=profile, vacancy=vacancy)
    WorkflowLog.objects.create(application=application, to_status=S.DRAFT, actor=user,
                               reason='Draft started')
    audit.record('application.draft_created', entity=application, actor=user,
                 changes={'vacancy': vacancy.reference})
    return application, True


def document_checklist(application):
    """[(requirement, [documents])] for the application's requirement version."""
    req = requirements_for(application)
    if req is None:
        return []
    documents = {}
    for doc in application.documents.select_related('document_type').prefetch_related('versions'):
        documents.setdefault(doc.document_type_id, []).append(doc)
    return [(item, documents.get(item.document_type_id, []))
            for item in req.document_requirements.select_related('document_type')]


def submission_problems(application):
    """What blocks submission (FR-006): deadline, profile, documents, file
    checks. Does not substitute for HR verification."""
    problems = []
    if not application.vacancy.is_accepting_applications():
        problems.append('The vacancy is not accepting applications (check the deadline).')
    problems += [f'Profile: {item}' for item in application.applicant.missing_information()]
    for requirement, docs in document_checklist(application):
        usable = [d for d in docs if (v := d.current_version()) and v.is_usable]
        name = requirement.document_type.name
        if requirement.is_required and not usable:
            problems.append(f'Upload the required document: {name}.')
        if any(not d.current_version().is_usable for d in docs if d.current_version()):
            problems.append(f'{name}: replace the file that failed the technical checks.')
        if len(docs) > requirement.max_files:
            problems.append(f'{name}: at most {requirement.max_files} file(s) allowed.')
    return problems


def _record_items(queryset, fields):
    return [{'source_record_id': str(r.pk), **audit.snapshot(r, fields)} for r in queryset]


def build_snapshot_data(application, requirement_version):
    profile = application.applicant
    user = profile.user
    education = sorted(profile.educational_records.all(),
                       key=lambda e: (DEGREE_RANK.get(e.degree_level, 0), e.created_at))
    fields = {k: [name for name, *_ in fact_schema.FACT_FIELDS[k]] for k in fact_schema.FACT_FIELDS}
    documents = []
    for doc in application.documents.select_related('document_type'):
        version = doc.current_version()
        if version:
            documents.append({'document_id': str(doc.pk), 'version_id': str(version.pk),
                              'version': version.version,
                              'document_type': doc.document_type.code,
                              'document_type_name': doc.document_type.name,
                              'file_name': version.file_name, 'sha256': version.sha256})
    return {
        'schema_version': fact_schema.SCHEMA_VERSION,
        'vacancy': {'id': str(application.vacancy_id), 'reference': application.vacancy.reference,
                    'position_title': application.vacancy.position_title},
        'requirement_version': requirement_version.version,
        'identity': {
            'source_record_id': 'identity',
            'last_name': user.last_name, 'first_name': user.first_name,
            'middle_name': user.middle_name, 'email': user.email,
            **audit.snapshot(profile, ['contact_no', 'birthdate', 'sex', 'address',
                                       'civil_status']),
        },
        'education': _record_items(education, fields['education']),
        'experience': _record_items(profile.work_experiences.all(), fields['experience']),
        'training': _record_items(profile.trainings.all(), fields['training']),
        'eligibility': _record_items(profile.eligibilities.all(), fields['eligibility']),
        'license': _record_items(profile.professional_licenses.all(), fields['license']),
        'documents': documents,
    }


def _capture_snapshot(application, snapshot_type, actor, requirement_version):
    version = (application.snapshots.order_by('-version').values_list('version', flat=True)
               .first() or 0) + 1
    return ApplicationSnapshot.objects.create(
        application=application, requirement_version=requirement_version, version=version,
        snapshot_type=snapshot_type, data=build_snapshot_data(application, requirement_version),
        captured_by=actor)


@transaction.atomic
def submit(user, application):
    """Submit a draft atomically. Returns (application, already_submitted).
    A repeated request returns the existing submission instead of duplicating."""
    # Vacancy-level lock first: the roster approval (Stage 6) uses the same lock.
    Vacancy.objects.select_for_update().get(pk=application.vacancy_id)
    application = _lock(application)
    _require_owner(user, application)
    if application.status != S.DRAFT:
        if application.submitted_at:
            return application, True
        raise ValidationError('This application cannot be submitted.')
    problems = submission_problems(application)
    if problems:
        raise ValidationError(problems)

    requirement_version = application.vacancy.current_requirements()
    snapshot = _capture_snapshot(application, SnapshotType.SUBMISSION, user, requirement_version)
    if application.submitted_at is None:
        application.submitted_at = timezone.now()
        application.save(update_fields=['submitted_at'])
    transition(application, S.SUBMITTED, actor=user, reason='Application submitted')
    sync_facts(application, snapshot)
    from documents.services import link_profile_evidence
    link_profile_evidence(application, user)
    _bump_revision(application)
    audit.record('application.submitted', entity=application, actor=user,
                 changes={'snapshot_version': snapshot.version,
                          'requirement_version': requirement_version.version})
    events.application_submitted(application)
    return application, False


# --------------------------------------------------------------------------
# Facts derived from snapshots
# --------------------------------------------------------------------------

def _current_facts(application, fact_type):
    return application.facts.filter(fact_type=fact_type).exclude(
        review_status=FactReviewStatus.SUPERSEDED)


def sync_facts(application, snapshot):
    """Create pending facts for new or changed snapshot items; supersede facts
    whose source item changed or was removed. Unchanged facts (and their review
    outcome) are kept. Facts HR entered manually or accepted from an AI
    extraction are never touched."""
    data = snapshot.data
    groups = {'identity': [data['identity']]}
    groups.update({kind: data.get(kind, []) for kind in fact_schema.PROFILE_KINDS})
    for fact_type, items in groups.items():
        current = {f.value.get('source_record_id'): f for f in _current_facts(application, fact_type)
                   if not fact_schema.is_reviewer_entered(f.value)}
        seen = set()
        for item in items:
            key = item['source_record_id']
            seen.add(key)
            old = current.get(key)
            if old and fact_schema.comparable(old.value) == fact_schema.comparable(item):
                continue
            new = ApplicationFact.objects.create(
                application=application, fact_type=fact_type, value=item,
                source_snapshot=snapshot, supersedes=old,
                relevance_status=(RelevanceStatus.NOT_ASSESSED
                                  if fact_type in fact_schema.RELEVANCE_TYPES
                                  else RelevanceStatus.NOT_APPLICABLE))
            if old:
                _supersede(old, f'Changed in snapshot {snapshot.version}')
                _copy_evidence(old, new)
        for key, old in current.items():
            if key not in seen:
                _supersede(old, f'Removed by applicant in snapshot {snapshot.version}')


def _supersede(fact, reason):
    fact.review_status = FactReviewStatus.SUPERSEDED
    fact.review_reason = (fact.review_reason + '\n' if fact.review_reason else '') + reason
    fact.save(update_fields=['review_status', 'review_reason'])


def _copy_evidence(old, new):
    from documents.models import FactEvidence
    for link in old.evidence.all():
        FactEvidence.objects.get_or_create(application_fact=new,
                                           document_version=link.document_version,
                                           defaults={'source_location': link.source_location})


# --------------------------------------------------------------------------
# HR review: status, facts, qualification outcomes
# --------------------------------------------------------------------------

@transaction.atomic
def start_review(actor, application):
    application = _lock(application)
    _require_reviewer(actor, application)
    transition(application, S.UNDER_REVIEW, actor=actor, reason='HR review started')
    return application


@transaction.atomic
def review_fact(actor, fact, *, decision, reason=''):
    application = _lock(fact.application)
    _require_reviewer(actor, application)
    fact = ApplicationFact.objects.select_for_update().get(pk=fact.pk)
    if not fact.is_current:
        raise ValidationError('This fact was superseded; review the current version.')
    if decision not in (FactReviewStatus.ACCEPTED, FactReviewStatus.REJECTED):
        raise ValidationError('Choose accept or reject.')
    if decision == FactReviewStatus.REJECTED and not reason:
        raise ValidationError('A reason is required to reject a fact.')
    old = fact.review_status
    fact.review_status = decision
    fact.reviewed_by = actor
    fact.reviewed_at = timezone.now()
    fact.review_reason = reason
    fact.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at', 'review_reason'])
    _bump_revision(application)
    audit.record(f'fact.{decision}', entity=fact, actor=actor, reason=reason,
                 changes={'review_status': {'old': old, 'new': decision},
                          'application': application.reference})
    return fact


@transaction.atomic
def set_relevance(actor, fact, relevance, *, reason=''):
    application = _lock(fact.application)
    _require_reviewer(actor, application)
    if fact.fact_type not in fact_schema.RELEVANCE_TYPES:
        raise ValidationError('Relevance applies to experience and training only.')
    if relevance not in (RelevanceStatus.RELEVANT, RelevanceStatus.NOT_RELEVANT):
        raise ValidationError('Choose relevant or not relevant.')
    old = fact.relevance_status
    fact.relevance_status = relevance
    fact.reviewed_by = actor
    fact.reviewed_at = timezone.now()
    fact.save(update_fields=['relevance_status', 'reviewed_by', 'reviewed_at'])
    _bump_revision(application)
    audit.record('fact.relevance_set', entity=fact, actor=actor, reason=reason,
                 changes={'relevance_status': {'old': old, 'new': relevance}})
    return fact


@transaction.atomic
def correct_fact(actor, fact, new_value, *, reason):
    """HR correction: a new accepted fact supersedes the old one, with
    field-level history (old/new value, actor, time, reason)."""
    application = _lock(fact.application)
    _require_reviewer(actor, application)
    if not reason:
        raise ValidationError('A reason is required for a correction.')
    fact = ApplicationFact.objects.select_for_update().get(pk=fact.pk)
    if not fact.is_current:
        raise ValidationError('This fact was already superseded.')
    value = {**{k: v for k, v in fact.value.items() if k in fact_schema.BOOKKEEPING_KEYS},
             **new_value}
    changes = audit.diff(fact_schema.comparable(fact.value), fact_schema.comparable(value))
    if not changes:
        raise ValidationError('No values were changed.')
    new = ApplicationFact.objects.create(
        application=application, fact_type=fact.fact_type, value=value,
        review_status=FactReviewStatus.ACCEPTED, relevance_status=fact.relevance_status,
        source_snapshot=fact.source_snapshot, supersedes=fact, reviewed_by=actor,
        reviewed_at=timezone.now(), review_reason=reason)
    _supersede(fact, f'Corrected by HR: {reason}')
    _copy_evidence(fact, new)
    for field, change in changes.items():
        ApplicationCorrection.objects.create(
            application=application, target_entity=ApplicationFact._meta.label,
            target_id=str(fact.pk), field_name=f'{fact.fact_type}.{field}',
            old_value=change['old'], new_value=change['new'], reason=reason, actor=actor)
    _bump_revision(application)
    audit.record('fact.corrected', entity=new, actor=actor, reason=reason, changes=changes)
    return new


@transaction.atomic
def add_manual_fact(actor, application, fact_type, value, *, reason, evidence_version=None):
    """HR-entered fact with source evidence (manual path that works without AI)."""
    application = _lock(application)
    _require_reviewer(actor, application)
    if fact_type not in fact_schema.FACT_FIELDS or fact_type == FactType.IDENTITY:
        raise ValidationError('Choose a qualification type.')
    if not reason:
        raise ValidationError('Explain where this information comes from.')
    fact = ApplicationFact.objects.create(
        application=application, fact_type=fact_type, value={**value, 'manual': True},
        review_status=FactReviewStatus.ACCEPTED,
        relevance_status=(RelevanceStatus.NOT_ASSESSED if fact_type in fact_schema.RELEVANCE_TYPES
                          else RelevanceStatus.NOT_APPLICABLE),
        reviewed_by=actor, reviewed_at=timezone.now(), review_reason=reason)
    if evidence_version is not None:
        link_evidence(actor, fact, evidence_version)
    _bump_revision(application)
    audit.record('fact.added_manually', entity=fact, actor=actor, reason=reason,
                 changes={'fact_type': fact_type, 'value': value})
    return fact


@transaction.atomic
def link_evidence(actor, fact, version, *, location=None):
    from documents.models import FactEvidence
    _require_reviewer(actor, fact.application)
    if version.document.application_id != fact.application_id:
        raise ValidationError('Evidence must come from the same application.')
    link, created = FactEvidence.objects.get_or_create(
        application_fact=fact, document_version=version,
        defaults={'source_location': location or {}})
    if created:
        audit.record('fact.evidence_linked', entity=fact, actor=actor,
                     changes={'document_version': str(version.pk), 'file': version.file_name})
    return link


def qualification_outcomes(application):
    """{standard_id: current assessment fact} for the application."""
    return {f.value.get('standard_id'): f
            for f in _current_facts(application, FactType.QUALIFICATION_ASSESSMENT)}


@transaction.atomic
def record_qualification_outcome(actor, application, standard, *, outcome, reason='',
                                 supporting_facts=(), check_result=None):
    """Human-entered outcome per standard: Meets / Does Not Meet / Needs Review
    / Not Required, with the requirement and supporting evidence recorded."""
    application = _lock(application)
    _require_reviewer(actor, application)
    if standard.requirement_version_id != requirements_for(application).pk:
        raise ValidationError('This standard is not part of the requirements for this '
                              'application.')
    if outcome not in fact_schema.OUTCOME_LABELS:
        raise ValidationError('Choose an outcome.')
    if outcome in ('does_not_meet', 'needs_review') and not reason:
        raise ValidationError('Explain the outcome.')
    supporting = [f for f in supporting_facts if f.application_id == application.pk]
    previous = qualification_outcomes(application).get(str(standard.pk))
    value = {'standard_id': str(standard.pk), 'criterion_type': standard.criterion_type,
             'requirement': standard.description, 'outcome': outcome, 'reason': reason,
             'supporting_fact_ids': [str(f.pk) for f in supporting]}
    if check_result is not None:  # HR started from the advisory check (13.4)
        value['advisory_check'] = {'result_id': str(check_result.pk),
                                   'result': check_result.result}
    fact = ApplicationFact.objects.create(
        application=application, fact_type=FactType.QUALIFICATION_ASSESSMENT, value=value,
        review_status=FactReviewStatus.ACCEPTED, supersedes=previous, reviewed_by=actor,
        reviewed_at=timezone.now(), review_reason=reason)
    if previous:
        _supersede(previous, 'Replaced by a newer assessment')
    _bump_revision(application)
    audit.record('qualification.assessed', entity=fact, actor=actor, reason=reason,
                 changes={'standard': standard.description, 'outcome': outcome,
                          'previous': previous.value.get('outcome') if previous else None,
                          'advisory_check': value.get('advisory_check')},
                 job=check_result.screening.job if check_result is not None else None)
    return fact


# --------------------------------------------------------------------------
# Corrections (FR-025)
# --------------------------------------------------------------------------

def default_correction_deadline():
    return timezone.now() + timedelta(days=settings.CORRECTION_WINDOW_DAYS)


@transaction.atomic
def request_correction(actor, application, *, message, document_types=(), deadline_at):
    application = _lock(application)
    _require_reviewer(actor, application)
    if application.status not in (S.SUBMITTED, S.UNDER_REVIEW):
        raise ValidationError('Corrections can be requested for submitted applications '
                              'under review.')
    if application.open_correction_request():
        raise ValidationError('There is already an open correction request.')
    if deadline_at <= timezone.now():
        raise ValidationError('The correction deadline must be in the future.')
    request = CorrectionRequest.objects.create(
        application=application, requested_by=actor, deadline_at=deadline_at,
        requested_changes={'schema_version': 1, 'message': message,
                           'document_types': [d.code for d in document_types]})
    transition(application, S.CORRECTION_REQUESTED, actor=actor, reason=message)
    audit.record('correction.requested', entity=request, actor=actor, reason=message,
                 changes={'application': application.reference,
                          'deadline_at': deadline_at.isoformat(),
                          'document_types': request.document_type_codes})
    events.correction_requested(request)
    return request


@transaction.atomic
def grant_correction_exception(actor, request, *, deadline_at, reason):
    """Authorized late-correction exception: extends the window, with a reason."""
    _require_reviewer(actor, request.application)
    request = CorrectionRequest.objects.select_for_update().get(pk=request.pk)
    if request.status != CorrectionStatus.OPEN:
        raise ValidationError('Only open correction requests can be extended.')
    if not reason:
        raise ValidationError('A reason is required for a correction exception.')
    if deadline_at <= timezone.now():
        raise ValidationError('The new deadline must be in the future.')
    old = request.deadline_at
    request.deadline_at = deadline_at
    request.exception_reason = reason
    request.save(update_fields=['deadline_at', 'exception_reason'])
    audit.record('correction.exception_granted', entity=request, actor=actor, reason=reason,
                 changes={'deadline_at': {'old': old.isoformat(), 'new': deadline_at.isoformat()}})
    return request


def correction_problems(application, request):
    problems = []
    if not request.window_open():
        problems.append('The correction window has closed. Contact HR for an exception.')
    problems += [f'Profile: {item}' for item in application.applicant.missing_information()]
    replaced = set(request.document_versions.values_list('document__document_type__code',
                                                         flat=True))
    for code in request.document_type_codes:
        if code not in replaced:
            problems.append(f'Upload a replacement for the requested document ({code}).')
    for requirement, docs in document_checklist(application):
        if requirement.is_required and not any(
                (v := d.current_version()) and v.is_usable for d in docs):
            problems.append(f'Upload the required document: {requirement.document_type.name}.')
    return problems


@transaction.atomic
def resubmit_corrections(user, application):
    application = _lock(application)
    _require_owner(user, application)
    request = application.open_correction_request()
    if application.status != S.CORRECTION_REQUESTED or request is None:
        raise ValidationError('There is no open correction request.')
    problems = correction_problems(application, request)
    if problems:
        raise ValidationError(problems)
    snapshot = _capture_snapshot(application, SnapshotType.CORRECTION, user,
                                 requirements_for(application))
    request.status = CorrectionStatus.SUBMITTED
    request.submitted_at = timezone.now()
    request.save(update_fields=['status', 'submitted_at'])
    sync_facts(application, snapshot)
    from documents.services import link_profile_evidence
    link_profile_evidence(application, user)
    transition(application, S.UNDER_REVIEW, actor=user, reason='Corrections submitted')
    _bump_revision(application)
    audit.record('correction.submitted', entity=request, actor=user,
                 changes={'snapshot_version': snapshot.version})
    events.corrections_submitted(request)
    return application


# --------------------------------------------------------------------------
# Withdrawal and reopening (FR-025, FR-013)
# --------------------------------------------------------------------------

@transaction.atomic
def withdraw(user, application, *, reason=''):
    application = _lock(application)
    _require_owner(user, application)
    if application.status not in WITHDRAWABLE:
        raise ValidationError('This application can no longer be withdrawn.')
    application.correction_requests.filter(status=CorrectionStatus.OPEN).update(
        status=CorrectionStatus.CANCELED)
    application.withdrawn_at = timezone.now()
    application.save(update_fields=['withdrawn_at'])
    transition(application, S.WITHDRAWN, actor=user, reason=reason or 'Withdrawn by applicant')
    events.application_withdrawn(application)
    _bump_revision(application)
    return application


@transaction.atomic
def reopen(actor, application, *, reason):
    """Authorized reopening of a concluded application, with a reason."""
    application = _lock(application)
    _require_reviewer(actor, application)
    if application.status not in CONCLUDED:
        raise ValidationError('Only withdrawn or concluded applications can be reopened.')
    if not reason:
        raise ValidationError('A reason is required to reopen an application.')
    application.withdrawn_at = None
    application.save(update_fields=['withdrawn_at'])
    transition(application, S.UNDER_REVIEW, actor=actor, reason=reason,
               visibility=WorkflowVisibility.APPLICANT)
    _bump_revision(application)
    return application
