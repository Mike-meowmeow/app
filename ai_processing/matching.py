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
- Evidence passages (added 26 November 2026): the text the AI read from the
  application's verified, current documents is split into short passages,
  and for each competency the closest ones are kept with their document and
  page. They are shown as "not reviewed by HR" to help HR find qualifications
  to review, and never count toward the similarity of reviewed facts. The
  applicant's own name, email addresses, and long numbers are masked first.
"""

import math
import re

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from applicants.models import DegreeLevel
from audit import services as audit
from recruitment import application_selectors as access
from recruitment.models import Application, FactReviewStatus, RelevanceStatus

from . import engine
from .models import (AIExtraction, AIJob, AIJobAttempt, AttemptStatus, CompetencyEvidence,
                     CompetencyMatch, JobStatus, JobType)
from .services import NOT_PROCESSED, _fail, _hash, _invalidate, heartbeat

MATCHER_VERSION = 'competency-matching-1'
MATCHED_TYPES = ('education', 'experience', 'training', 'eligibility', 'license')
MAX_TEXT_CHARACTERS = 1000  # the model reads about 256 word pieces; longer text is cut
DEGREE_LABELS = dict(DegreeLevel.choices)
PASSAGE_VERSION = 'document-passages-1'
PASSAGE_MIN_CHARACTERS = 30
PASSAGE_MAX_CHARACTERS = 300
MAX_PASSAGES = 300  # per application, to bound the worker's time
EVIDENCE_PER_COMPETENCY = 3
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
LONG_NUMBER = re.compile(r'\+?\d[\d\s().-]{5,}\d')
WORD = re.compile(r'[^\W\d_]+')


def enabled():
    return settings.AI_COMPETENCY_MATCHING_ENABLED


def configuration():
    return {'model': settings.SBERT_MODEL, 'similarity': 'cosine of unit-length embeddings',
            'facts': 'accepted; experience and training not marked not relevant',
            'max_text_characters': MAX_TEXT_CHARACTERS,
            'documents': {'passages': PASSAGE_VERSION, 'max_passages': MAX_PASSAGES,
                          'per_competency': EVIDENCE_PER_COMPETENCY,
                          'min_similarity': settings.AI_EVIDENCE_MIN_SIMILARITY}}


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


def document_extractions(application):
    """The AI extractions of the application's current, verified documents."""
    from documents.models import VerificationStatus
    found = (AIExtraction.objects
             .filter(job__application=application, job__job_type=JobType.DOCUMENT_EXTRACTION,
                     job__invalidated_at__isnull=True,
                     document_version__document__application=application,
                     document_version__verification_status=VerificationStatus.VERIFIED)
             .select_related('document_version__document__document_type')
             .order_by('document_version__document__document_type__name',
                       'document_version__version', 'pk'))
    return [e for e in found
            if e.document_version.document.current_version().pk == e.document_version_id]


def _masker(application):
    user = application.applicant.user
    names = {w.lower() for part in (user.first_name, user.middle_name, user.last_name)
             for w in WORD.findall(part or '') if len(w) > 1}

    def mask(text):
        text = EMAIL.sub('[email]', text)
        text = LONG_NUMBER.sub('[number]', text)
        return WORD.sub(lambda m: '[name]' if m.group().lower() in names else m.group(), text)
    return mask


def split_passages(text):
    """Short passages of page text: sentences and lines, joined until long
    enough, cut when too long. Fragments with few letters are dropped."""
    pieces = [' '.join(p.split()) for p in re.split(r'(?<=[.;:])\s+|\n+', text or '')]
    passages, current = [], ''
    for piece in (p for p in pieces if p):
        current = f'{current} {piece}' if current else piece
        if len(current) >= PASSAGE_MIN_CHARACTERS:
            passages.append(current[:PASSAGE_MAX_CHARACTERS])
            current = ''
    if current:
        if passages:
            passages[-1] = f'{passages[-1]} {current}'[:PASSAGE_MAX_CHARACTERS]
        else:
            passages.append(current)
    return [p for p in passages if sum(c.isalpha() for c in p) >= 10]


def document_passages(application, extractions=None):
    """[{'version': DocumentVersion, 'page': n, 'text': ...}] from the verified
    documents' extracted text, masked, at most MAX_PASSAGES."""
    mask = _masker(application)
    passages = []
    for extraction in document_extractions(application) if extractions is None else extractions:
        for page in extraction.pages or []:
            for text in split_passages(page.get('text', '')):
                passages.append({'version': extraction.document_version,
                                 'page': page.get('page'), 'text': mask(text)})
                if len(passages) >= MAX_PASSAGES:
                    return passages
    return passages


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
    extractions = document_extractions(application)
    manifest = {
        'matcher_version': MATCHER_VERSION, 'configuration': configuration(),
        'snapshot': str(snapshot.pk), 'requirement_version': str(snapshot.requirement_version_id),
        'competencies': [{'id': str(c.pk), 'text': competency_text(c)} for c in competencies],
        'facts': [{'id': str(f.pk), 'fact_type': f.fact_type, 'text': fact_text(f)}
                  for f in facts],
        # Verified documents whose extracted text gives evidence passages.
        'documents': [{'version': str(e.document_version_id), 'extraction': str(e.pk)}
                      for e in extractions],
    }
    return snapshot, competencies, facts, manifest


def problems(application):
    """Why matching cannot be queued now ([] when it can)."""
    if not enabled():
        return ['Competency matching is turned off (AI_COMPETENCY_MATCHING_ENABLED).']
    if application.status in NOT_PROCESSED:
        return ['Matching runs on submitted applications only.']
    snapshot, competencies, facts, manifest = check_inputs(application)
    if snapshot is None:
        return ['This application has no submitted information.']
    found = []
    if not competencies:
        found.append('The published requirements list no competencies.')
    if not facts and not manifest['documents']:
        found.append('No accepted qualification facts or verified documents read by the AI to '
                     'compare yet. Review the facts first.')
    from recruitment import review_gate
    return found + review_gate.problems(application)


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
        passages = document_passages(application, _extractions(job))
        texts = ([competency_text(c) for c in competencies] + [fact_text(f) for f in facts]
                 + [p['text'] for p in passages])
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
        fact_vectors = vectors[len(competencies):len(competencies) + len(facts)]
        passage_vectors = vectors[len(competencies) + len(facts):]
        scores = [(c, f, cosine(cv, fv)) for c, cv in zip(competencies, competency_vectors)
                  for f, fv in zip(facts, fact_vectors)]
        evidence = []
        for c, cv in zip(competencies, competency_vectors):
            ranked = sorted(((cosine(cv, pv), p) for p, pv in zip(passages, passage_vectors)),
                            key=lambda pair: -pair[0])
            kept = [(score, p) for score, p in ranked
                    if score >= settings.AI_EVIDENCE_MIN_SIMILARITY][:EVIDENCE_PER_COMPETENCY]
            evidence += [(c, p, score, rank) for rank, (score, p) in enumerate(kept, start=1)]
        return _store(attempt, scores, identity, evidence)
    return None


def _extractions(job):
    """The extractions listed in the job's manifest, in that order."""
    ids = [d['extraction'] for d in job.input_manifest.get('documents', [])]
    found = {str(e.pk): e for e in AIExtraction.objects.filter(pk__in=ids)
             .select_related('document_version__document__document_type')}
    return [found[i] for i in ids if i in found]


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


def _store(attempt, scores, identity, evidence=()):
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
        for competency, passage, similarity, rank in evidence:
            CompetencyEvidence.objects.create(
                job=job, vacancy_competency=competency, document_version=passage['version'],
                page_number=passage['page'], excerpt=passage['text'],
                similarity=round(similarity, 4), rank=rank)
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
                              'pairs': len(scores), 'evidence_passages': len(evidence),
                              'model': job.model_version})
    return job


# --------------------------------------------------------------------------
# Display helpers
# --------------------------------------------------------------------------

def by_competency(job, top=None):
    """[(competency, [match, ...])] with the closest matches first."""
    if job is None:
        return []
    from recruitment.models import VacancyCompetency
    groups = {}
    order = []
    ids = [c['id'] for c in job.input_manifest.get('competencies', [])]
    for competency in VacancyCompetency.objects.filter(pk__in=ids).order_by('created_at', 'pk'):
        groups[competency.pk] = (competency, [])  # also competencies with evidence only
        order.append(competency.pk)
    for match in (job.competency_matches.select_related('vacancy_competency', 'application_fact')
                  .order_by('vacancy_competency__created_at', '-similarity')):
        match.text = fact_text(match.application_fact)
        key = match.vacancy_competency_id
        if key not in groups:
            groups[key] = (match.vacancy_competency, [])
            order.append(key)
        groups[key][1].append(match)
    return [(groups[k][0], groups[k][1][:top] if top else groups[k][1]) for k in order]


def evidence_by_competency(job):
    """{competency id: [evidence, ...]} closest first."""
    found = {}
    if job is None:
        return found
    for item in job.competency_evidence.select_related(
            'document_version__document__document_type').order_by('rank'):
        found.setdefault(item.vacancy_competency_id, []).append(item)
    return found
