"""SBERT competency matching (13.5, FR-012).

Each competency of the application's published requirement version is
compared with the qualification text HR accepted, using sentence embeddings
(SBERT) and cosine similarity. The result is, for each competency, how close
in meaning each qualification entry is, on the model's own scale from -1 to 1,
with the exact text compared.

Rules this module keeps:
- Inputs are accepted, current facts only; experience and training that HR
  marked not relevant are left out. Text is built only from qualification
  fields (degree and program, position and duties, training title, eligibility,
  profession). Names, contact details, birthdate, sex, and the names of
  schools and employers are never part of the input.
- The job runs in the AI worker, where the model is installed. It records the
  model name, revision, and library versions, and the exact texts and their
  hash. If the inputs change before the results are stored, the results are
  discarded; if they change afterwards, the result is shown as out of date.
- Similarity is not a hiring score, a probability, or a ranking. No threshold
  has been validated with HR; values are compared within one application.
  Nothing here changes the application, its revision, the shortlist, the
  decisions, or the official roster.
"""

import math

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from applicants.models import DegreeLevel
from audit import services as audit
from recruitment import application_selectors as access
from recruitment.models import Application, FactReviewStatus, RelevanceStatus

from . import engine
from .models import (AIJob, AIJobAttempt, AttemptStatus, CompetencyMatch, JobStatus, JobType)
from .services import NOT_PROCESSED, _fail, _hash, _invalidate, heartbeat

MATCHER_VERSION = 'competency-matching-1'
MATCHED_TYPES = ('education', 'experience', 'training', 'eligibility', 'license')
MAX_TEXT_CHARACTERS = 1000  # the model reads about 256 word pieces; longer text is cut
DEGREE_LABELS = dict(DegreeLevel.choices)


def enabled():
    return settings.AI_COMPETENCY_MATCHING_ENABLED


def configuration():
    return {'model': settings.SBERT_MODEL, 'similarity': 'cosine of unit-length embeddings',
            'facts': 'accepted; experience and training not marked not relevant',
            'max_text_characters': MAX_TEXT_CHARACTERS}


def _clean(*parts):
    text = '. '.join(' '.join(str(p).split()) for p in parts if p and str(p).strip())
    return text[:MAX_TEXT_CHARACTERS]


def fact_text(fact):
    """Qualification text compared with competencies ('' when there is none).
    Built from qualification fields only; see the module notes."""
    v = fact.value
    if fact.fact_type == 'education':
        level = DEGREE_LABELS.get(v.get('degree_level'), '')
        return _clean(f"{level} in {v.get('program')}" if v.get('program') else level)
    if fact.fact_type == 'experience':
        return _clean(v.get('position'), v.get('duties'))
    if fact.fact_type == 'training':
        return _clean(v.get('title'))
    if fact.fact_type == 'eligibility':
        return _clean(v.get('eligibility_type'))
    if fact.fact_type == 'license':
        return _clean(f"{v.get('profession')} license" if v.get('profession') else '')
    return ''


def competency_text(competency):
    return _clean(competency.name, competency.description)


def check_inputs(application):
    """(snapshot, competencies, facts, manifest); facts are those with text."""
    snapshot = application.latest_snapshot()
    if snapshot is None:
        return None, [], [], {}
    competencies = list(snapshot.requirement_version.competencies.order_by('created_at', 'pk'))
    candidates = (application.facts.filter(fact_type__in=MATCHED_TYPES,
                                           review_status=FactReviewStatus.ACCEPTED)
                  .exclude(relevance_status=RelevanceStatus.NOT_RELEVANT)
                  .order_by('fact_type', 'created_at', 'pk'))
    facts = [f for f in candidates if fact_text(f)]
    manifest = {
        'matcher_version': MATCHER_VERSION, 'configuration': configuration(),
        'snapshot': str(snapshot.pk), 'requirement_version': str(snapshot.requirement_version_id),
        'competencies': [{'id': str(c.pk), 'text': competency_text(c)} for c in competencies],
        'facts': [{'id': str(f.pk), 'fact_type': f.fact_type, 'text': fact_text(f)}
                  for f in facts],
    }
    return snapshot, competencies, facts, manifest


def problems(application):
    """Why matching cannot be queued now ([] when it can)."""
    if not enabled():
        return ['Competency matching is turned off (AI_COMPETENCY_MATCHING_ENABLED).']
    if application.status in NOT_PROCESSED:
        return ['Matching runs on submitted applications only.']
    snapshot, competencies, facts, _ = check_inputs(application)
    if snapshot is None:
        return ['This application has no submitted information.']
    found = []
    if not competencies:
        found.append('The published requirements list no competencies.')
    if not facts:
        found.append('No accepted qualification facts to compare yet. Review the facts first.')
    return found


def latest(application):
    """The newest matching job of the application that was not replaced."""
    return (AIJob.objects.filter(application=application, job_type=JobType.COMPETENCY_MATCH,
                                 invalidated_at__isnull=True)
            .select_related('requested_by').order_by('-queued_at').first())


def is_current(job):
    *_, manifest = check_inputs(job.application)
    return bool(manifest) and _hash(manifest) == job.input_hash


@transaction.atomic
def queue_matching(actor, application):
    """Queue SBERT matching for the application. Returns (job, created); with
    unchanged inputs the existing job is returned."""
    application = Application.objects.select_for_update().get(pk=application.pk)
    if not access.can_review(actor, application):
        raise PermissionDenied
    found = problems(application)
    if found:
        raise ValidationError(found)
    snapshot, competencies, facts, manifest = check_inputs(application)
    input_hash = _hash(manifest)
    existing = (AIJob.objects.filter(application=application, job_type=JobType.COMPETENCY_MATCH,
                                     input_hash=input_hash, invalidated_at__isnull=True)
                .exclude(status=JobStatus.FAILED).first())
    if existing:
        return existing, False
    _invalidate(AIJob.objects.filter(application=application,
                                     job_type=JobType.COMPETENCY_MATCH),
                'Replaced by a newer matching run.', actor)
    generation = AIJob.objects.filter(application=application,
                                      job_type=JobType.COMPETENCY_MATCH).count()
    job = AIJob.objects.create(
        application=application, input_snapshot=snapshot, job_type=JobType.COMPETENCY_MATCH,
        requested_by=actor, model_name=settings.SBERT_MODEL, model_version=MATCHER_VERSION,
        configuration=configuration(), input_manifest=manifest, input_hash=input_hash,
        deduplication_key=_hash({'type': JobType.COMPETENCY_MATCH,
                                 'application': str(application.pk), 'input': input_hash,
                                 'generation': generation}))
    audit.record('ai.job_queued', entity=job, actor=actor, job=job,
                 changes={'application': application.reference, 'job_type': job.job_type,
                          'competencies': len(competencies), 'facts': len(facts)})
    return job, True


def cosine(u, v):
    dot = sum(a * b for a, b in zip(u, v))
    norm = math.sqrt(sum(a * a for a in u)) * math.sqrt(sum(b * b for b in v))
    return max(-1.0, min(1.0, dot / norm)) if norm else 0.0


def explanation(competency, fact, similarity):
    return (f'"{competency_text(competency)}" compared with {fact.get_fact_type_display()}: '
            f'"{fact_text(fact)}". Cosine similarity {similarity:.2f} on the model\'s scale from '
            '-1 to 1 (higher means closer in meaning).')


# --------------------------------------------------------------------------
# Worker
# --------------------------------------------------------------------------

def process(attempt, job):
    """Run one claimed matching attempt (called by services.process)."""
    application = job.application
    try:
        snapshot, competencies, facts, manifest = check_inputs(application)
        if job.invalidated_at or application.status in NOT_PROCESSED:
            raise engine.ExtractionError('The application is no longer eligible: '
                                         + (job.invalidation_reason or 'withdrawn or draft.'))
        if _hash(manifest) != job.input_hash:
            raise _InputsChanged()
        texts = ([competency_text(c) for c in competencies] + [fact_text(f) for f in facts])
        heartbeat(attempt)
        vectors = engine.embed(texts)
        heartbeat(attempt)
        identity = engine.sbert_identity()
    except _InputsChanged:
        return _discard(attempt, 'The facts or requirements changed after matching was queued; '
                                 'queue it again.')
    except engine.EngineUnavailable as exc:
        _fail(attempt, 'runtime_missing', str(exc), retryable=True)
    except engine.ExtractionError as exc:
        _fail(attempt, 'not_eligible', str(exc), retryable=False)
    except Exception as exc:  # unexpected: record it and retry
        _fail(attempt, 'error', f'{type(exc).__name__}: {exc}', retryable=True)
    else:
        competency_vectors = vectors[:len(competencies)]
        fact_vectors = vectors[len(competencies):]
        scores = [(c, f, cosine(cv, fv)) for c, cv in zip(competencies, competency_vectors)
                  for f, fv in zip(facts, fact_vectors)]
        return _store(attempt, scores, identity)
    return None


class _InputsChanged(Exception):
    pass


def _discard(attempt, message):
    with transaction.atomic():
        attempt = AIJobAttempt.objects.select_for_update().get(pk=attempt.pk)
        job = AIJob.objects.select_for_update().get(pk=attempt.job_id)
        if attempt.status != AttemptStatus.RUNNING:
            return None
        _invalidate(AIJob.objects.filter(pk=job.pk), message)
        job.refresh_from_db()
        now = timezone.now()
        attempt.status = AttemptStatus.FAILED
        attempt.finished_at = now
        attempt.error_code = 'invalidated'
        attempt.error_message = message
        attempt.save()
        job.status = JobStatus.FAILED
        job.completed_at = now
        job.last_error = message
        job.save(update_fields=['status', 'completed_at', 'last_error'])
    return None


def _store(attempt, scores, identity):
    with transaction.atomic():
        attempt = AIJobAttempt.objects.select_for_update().get(pk=attempt.pk)
        job = AIJob.objects.select_for_update().select_related('application').get(
            pk=attempt.job_id)
        Application.objects.select_for_update().get(pk=job.application_id)
        if attempt.status != AttemptStatus.RUNNING:
            return None  # recovered as abandoned meanwhile; another run owns the job
        if job.invalidated_at or not is_current(job):
            return _discard(attempt, 'The facts or requirements changed during matching; the '
                                     'results were discarded.')
        for competency, fact, similarity in scores:
            CompetencyMatch.objects.create(
                job=job, vacancy_competency=competency, application_fact=fact,
                similarity=round(similarity, 4),
                explanation=explanation(competency, fact, similarity))
        now = timezone.now()
        revision = identity.get('revision')
        job.model_version = (f"{MATCHER_VERSION}; {identity.get('model')}"
                             f"{f' @ {revision[:12]}' if revision else ''}; "
                             f"sentence-transformers {identity.get('sentence-transformers')}; "
                             f"torch {identity.get('torch')}")[:200]
        job.configuration = {**job.configuration, 'runtime': identity}
        job.status = JobStatus.COMPLETED
        job.completed_at = now
        job.last_error = ''
        job.save(update_fields=['model_version', 'configuration', 'status', 'completed_at',
                                'last_error'])
        attempt.status = AttemptStatus.SUCCEEDED
        attempt.finished_at = now
        attempt.save()
        audit.record('ai.competency_matching_completed', entity=job, actor=None, job=job,
                     changes={'application': job.application.reference,
                              'pairs': len(scores), 'model': job.model_version})
    return job


# --------------------------------------------------------------------------
# Display helpers
# --------------------------------------------------------------------------

def by_competency(job, top=None):
    """[(competency, [match, ...])] with the closest matches first."""
    if job is None:
        return []
    groups = {}
    order = []
    for match in (job.competency_matches.select_related('vacancy_competency', 'application_fact')
                  .order_by('vacancy_competency__created_at', '-similarity')):
        match.text = fact_text(match.application_fact)
        key = match.vacancy_competency_id
        if key not in groups:
            groups[key] = (match.vacancy_competency, [])
            order.append(key)
        groups[key][1].append(match)
    return [(groups[k][0], groups[k][1][:top] if top else groups[k][1]) for k in order]

