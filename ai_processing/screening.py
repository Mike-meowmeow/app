"""Running and storing advisory qualification checks (13.4).

A check is quick and needs no AI runtime, so it runs in the HR request rather
than the worker. It is still recorded as an AI job (job type, rules version,
exact inputs and their hash), so every result can be traced and reproduced.
Running it again with unchanged inputs returns the stored check; when facts or
reviews change, the next run stores a new check and marks the older one
replaced. Nothing here changes the application, its revision, its facts, the
shortlist, or the roster.
"""

from datetime import date

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit import services as audit
from core import credentials
from recruitment import application_selectors as access
from recruitment import fact_schema
from recruitment.models import Application, FactReviewStatus

from . import qualification as rules
from .models import (AIJob, AIJobAttempt, AIScreening, AttemptStatus, JobStatus, JobType,
                     StandardResult)
from .services import NOT_PROCESSED, _hash

MODEL_NAME = 'evsu-qualification-rules'
CONFIGURATION = {
    'facts_used': 'accepted only; pending facts reported',
    'relevance': 'experience and training count only when marked relevant',
    'experience_as_of': 'submission date; overlapping periods counted once',
    'days_per_year': str(rules.DAYS_PER_YEAR),
    'name_matching': 'every significant word of the requirement',
}
CHECKED_TYPES = [t for t in fact_schema.PROFILE_KINDS]


def enabled():
    return settings.AI_QUALIFICATION_CHECKS_ENABLED


def check_inputs(application):
    """(snapshot, standards, facts, manifest) used for a check. Identity facts
    (names, contact details, birthdate, sex) are never part of the inputs."""
    snapshot = application.latest_snapshot()
    if snapshot is None:
        return None, [], [], {}
    requirement = snapshot.requirement_version
    standards = list(requirement.qualification_standards.order_by('created_at', 'pk'))
    facts = list(application.facts.filter(fact_type__in=CHECKED_TYPES)
                 .exclude(review_status=FactReviewStatus.SUPERSEDED).order_by('created_at', 'pk'))
    as_of = timezone.localtime(snapshot.captured_at).date()
    manifest = {
        'rules_version': rules.RULES_VERSION,
        # The license/eligibility reference list: edits make results out of date.
        'credential_list': credentials.digest(),
        'snapshot': str(snapshot.pk), 'snapshot_version': snapshot.version,
        'requirement_version': str(requirement.pk),
        'requirement_version_number': requirement.version,
        'as_of': as_of.isoformat(),
        'standards': [{'id': str(s.pk), 'criterion_type': s.criterion_type,
                       'operator': s.operator, 'required_value': s.required_value,
                       'unit': s.unit} for s in standards],
        'facts': [{'id': str(f.pk), 'fact_type': f.fact_type, 'review_status': f.review_status,
                   'relevance_status': f.relevance_status,
                   'value': fact_schema.comparable(f.value)} for f in facts],
    }
    return snapshot, standards, facts, manifest


def problems(application):
    """Why a check cannot run now ([] when it can)."""
    if not enabled():
        return ['Qualification checks are turned off (AI_QUALIFICATION_CHECKS_ENABLED).']
    if application.status in NOT_PROCESSED:
        return ['Checks run on submitted applications only.']
    snapshot = application.latest_snapshot()
    if snapshot is None:
        return ['This application has no submitted information.']
    if not snapshot.requirement_version.qualification_standards.exists():
        return ['The published requirements have no qualification standards to check.']
    return []


def latest(application):
    """The newest check of the application that has not been replaced."""
    return (AIScreening.objects.filter(job__application=application,
                                       job__invalidated_at__isnull=True)
            .select_related('job', 'job__requested_by').order_by('-screened_at').first())


def inputs_hash(application):
    """Fingerprint of everything the checks read ('' before submission). A
    recorded qualification outcome stores it; when it changes, the outcome
    can be recorded again."""
    *_, manifest = check_inputs(application)
    return _hash(manifest) if manifest else ''


def is_current(screening):
    """True when the facts, reviews, and requirements are unchanged since."""
    *_, manifest = check_inputs(screening.job.application)
    return bool(manifest) and _hash(manifest) == screening.job.input_hash


def results_by_standard(screening):
    if screening is None:
        return {}
    return {str(r.standard_id): r for r in screening.results.all()}


@transaction.atomic
def run_checks(actor, application):
    """Run the checks for the application. Returns (screening, created)."""
    application = Application.objects.select_for_update().get(pk=application.pk)
    if not access.can_review(actor, application):
        raise PermissionDenied
    found = problems(application)
    if found:
        raise ValidationError(found)
    snapshot, standards, facts, manifest = check_inputs(application)
    input_hash = _hash(manifest)
    existing = (AIScreening.objects.filter(job__application=application,
                                           job__job_type=JobType.QUALIFICATION_CHECK,
                                           job__input_hash=input_hash,
                                           job__invalidated_at__isnull=True).first())
    if existing:
        return existing, False

    now = timezone.now()
    generation = AIJob.objects.filter(application=application,
                                      job_type=JobType.QUALIFICATION_CHECK).count()
    as_of = date.fromisoformat(manifest['as_of'])
    outcomes = [(s, rules.evaluate(s, facts, as_of)) for s in standards]
    overall = rules.overall(o.result for _, o in outcomes)
    job = AIJob.objects.create(
        application=application, input_snapshot=snapshot,
        job_type=JobType.QUALIFICATION_CHECK, status=JobStatus.COMPLETED, requested_by=actor,
        model_name=MODEL_NAME, model_version=rules.RULES_VERSION, configuration=CONFIGURATION,
        input_manifest=manifest, input_hash=input_hash,
        deduplication_key=_hash({'type': JobType.QUALIFICATION_CHECK,
                                 'application': str(application.pk), 'input': input_hash,
                                 'generation': generation}),
        attempt_count=1, queued_at=now, available_at=now, started_at=now, completed_at=now)
    AIJobAttempt.objects.create(job=job, attempt_number=1, worker_reference='web request',
                                status=AttemptStatus.SUCCEEDED, started_at=now,
                                heartbeat_at=now, finished_at=now)
    screening = AIScreening.objects.create(job=job, input_snapshot=snapshot,
                                           overall_status=overall, screened_at=now)
    for standard, outcome in outcomes:
        StandardResult.objects.create(screening=screening, standard=standard,
                                      result=outcome.result, evidence_fact_ids=outcome.evidence,
                                      explanation=outcome.explanation)
    (AIJob.objects.filter(application=application, job_type=JobType.QUALIFICATION_CHECK,
                          invalidated_at__isnull=True).exclude(pk=job.pk)
     .update(invalidated_at=now, invalidation_reason='Replaced by a newer check.'))
    audit.record('ai.qualification_checked', entity=application, actor=actor, job=job,
                 changes={'overall': overall, 'rules_version': rules.RULES_VERSION,
                          'results': {s.description: o.result for s, o in outcomes}})
    return screening, True


def usable_result(application, standard, result_id):
    """The check result HR may start an outcome from: same application and
    standard, from a check that is neither replaced nor out of date."""
    if not result_id:
        return None
    try:
        result = (StandardResult.objects.select_related('screening__job__application')
                  .get(pk=result_id, standard=standard,
                       screening__job__application=application,
                       screening__job__invalidated_at__isnull=True))
    except (StandardResult.DoesNotExist, ValidationError, ValueError):
        return None
    return result if is_current(result.screening) else None


def evidence_facts(application, result):
    """The facts a result used, in the stored order (missing ones skipped)."""
    by_id = {str(f.pk): f for f in application.facts.filter(pk__in=result.evidence_fact_ids)
             .prefetch_related('evidence__document_version__document__document_type')}
    return [by_id[i] for i in result.evidence_fact_ids if i in by_id]

