"""AI job processing and HR review of extracted items (FR-008, FR-024; 13.1-13.3).

Rules this module keeps:
- Only the current, verified, technically valid version of a document is
  processed; this is checked when queuing, when a worker starts, and again
  while storing results (under a row lock), so a replacement or a revoked
  verification during processing discards the results.
- One job per document version and verification: repeated requests return the
  existing job instead of creating duplicates. Jobs are rows in MySQL, created
  in the same transaction as the verification, so nothing is dispatched before
  the commit and nothing is lost if a worker is down.
- Failed runs are retried a limited number of times; a run whose worker stops
  (no heartbeat) is marked abandoned and retried.
- Results are proposals. They become application facts only when HR accepts,
  corrects, or links them; the original extraction is never edited.
- AI never changes an application's status, shortlist, decisions, or roster.
"""

import hashlib
import json
import os
import socket
from datetime import timedelta

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit import services as audit
from documents import storage
from documents.models import DocumentVersion, FactEvidence, VerificationStatus
from recruitment import application_selectors as access
from recruitment import application_services as applications
from recruitment import fact_schema
from recruitment.models import (ApplicationFact, ApplicationStatus, FactReviewStatus, FactType,
                                RelevanceStatus)

from . import engine, parsers
from .models import (AIExtraction, AIJob, AIJobAttempt, AttemptStatus, ExtractionItem,
                     ItemReviewStatus, JobStatus, JobType)

MODEL_NAME = 'evsu-document-extraction'
SUPPORTED_MIME_TYPES = {'application/pdf', 'image/jpeg', 'image/png'}
NOT_PROCESSED = {ApplicationStatus.DRAFT, ApplicationStatus.WITHDRAWN}
LOW_OCR_CONFIDENCE = 0.6
REVIEWABLE_TYPES = [t for t in fact_schema.PROFILE_KINDS]
# Fields that identify "the same" qualification when comparing with facts.
MATCH_KEYS = {
    'education': ('program',),
    'experience': ('position', 'organization'),
    'training': ('title',),
    'eligibility': ('eligibility_type',),
    'license': ('license_number', 'profession'),
}


def model_version():
    return f'{parsers.PARSER_VERSION}; text=pdfplumber; ocr=tesseract'


def _hash(data):
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _file_sha256(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------
# Eligibility and queuing (FR-008)
# --------------------------------------------------------------------------

def eligibility_problems(version):
    """Why this document version may not be processed now ([] when it may)."""
    document = version.document
    problems = []
    if version.verification_status != VerificationStatus.VERIFIED:
        problems.append('The document has not been verified by HR.')
    current = document.current_version()
    if current is None or current.pk != version.pk:
        problems.append('A newer version of this document was uploaded.')
    if not version.is_usable:
        problems.append('The file failed the technical checks.')
    if version.mime_type not in SUPPORTED_MIME_TYPES:
        problems.append('This file type cannot be read.')
    if document.document_type.code not in settings.AI_EXTRACTION_TYPES:
        problems.append(f'{document.document_type.name} documents are not processed by AI.')
    if document.application.status in NOT_PROCESSED:
        problems.append('The application is not submitted or was withdrawn.')
    return problems


def current_job(version):
    """The job that currently represents this version, if any."""
    return (AIJob.objects.filter(document_version=version, job_type=JobType.DOCUMENT_EXTRACTION,
                                 model_version=model_version(), invalidated_at__isnull=True)
            .order_by('-queued_at').first())


@transaction.atomic
def queue_extraction(actor, version, *, automatic=False):
    """Queue text extraction for a verified document version. Returns
    (job, created); a repeated request returns the existing job."""
    version = DocumentVersion.objects.select_for_update().select_related(
        'document__application', 'document__document_type').get(pk=version.pk)
    application = version.document.application
    if actor is not None and not access.can_review(actor, application):
        raise PermissionDenied('You are not assigned to review this vacancy.')
    problems = eligibility_problems(version)
    if problems:
        raise ValidationError(problems)
    existing = current_job(version)
    if existing:
        return existing, False
    verification = version.verifications.order_by('-reviewed_at', '-created_at').first()
    configuration = engine.configuration()
    manifest = {
        'document_version_id': str(version.pk), 'document_id': str(version.document_id),
        'version': version.version, 'document_type': version.document.document_type.code,
        'file_name': version.file_name, 'mime_type': version.mime_type,
        'sha256': version.sha256, 'verification_id': str(verification.pk) if verification else None,
    }
    generation = AIJob.objects.filter(document_version=version,
                                      invalidated_at__isnull=False).count()
    job = AIJob.objects.create(
        application=application, document_version=version,
        input_snapshot=application.latest_snapshot(), job_type=JobType.DOCUMENT_EXTRACTION,
        requested_by=actor, model_name=MODEL_NAME, model_version=model_version(),
        configuration=configuration, input_manifest=manifest,
        input_hash=_hash({'manifest': manifest, 'configuration': configuration}),
        deduplication_key=_hash({'type': JobType.DOCUMENT_EXTRACTION, 'version': str(version.pk),
                                 'verification': manifest['verification_id'],
                                 'model': model_version(), 'configuration': configuration,
                                 'generation': generation}))
    audit.record('ai.job_queued', entity=job, actor=actor, job=job,
                 changes={'application': application.reference,
                          'document_type': manifest['document_type'],
                          'version': version.version, 'automatic': automatic})
    return job, True


def _invalidate(jobs, reason, actor=None):
    now = timezone.now()
    count = 0
    for job in jobs.select_for_update().filter(invalidated_at__isnull=True):
        job.invalidated_at = now
        job.invalidation_reason = reason
        job.save(update_fields=['invalidated_at', 'invalidation_reason'])
        audit.record('ai.job_invalidated', entity=job, actor=actor, reason=reason, job=job,
                     changes={'status': job.status})
        count += 1
    return count


def on_verification_recorded(actor, version, old_status, new_status):
    """Called by documents.services.verify inside its transaction."""
    if old_status == VerificationStatus.VERIFIED and new_status != VerificationStatus.VERIFIED:
        _invalidate(AIJob.objects.filter(document_version=version),
                    f'Verification changed to {VerificationStatus(new_status).label}.', actor)
    if new_status == VerificationStatus.VERIFIED and settings.AI_EXTRACTION_ENABLED \
            and not eligibility_problems(version):
        queue_extraction(actor, version, automatic=True)


def on_document_replaced(actor, document, new_version):
    """Called by documents.services.upload inside its transaction."""
    _invalidate(AIJob.objects.filter(document_version__document=document)
                .exclude(document_version=new_version),
                f'Replaced by version {new_version.version}.', actor)


# --------------------------------------------------------------------------
# Worker (13.1, 13.2)
# --------------------------------------------------------------------------

# Job types the worker runs; qualification checks run in the HR request.
WORKER_JOB_TYPES = (JobType.DOCUMENT_EXTRACTION, JobType.COMPETENCY_MATCH)


def worker_reference():
    return f'{socket.gethostname()}:{os.getpid()}'


def _schedule_or_fail(job, message, *, retryable, attempt=None):
    """After a failed run: queue it again later, or mark it Failed."""
    now = timezone.now()
    job.last_error = message[:2000]
    if job.invalidated_at is None and retryable and job.attempt_count < settings.AI_MAX_ATTEMPTS:
        job.status = JobStatus.QUEUED
        job.available_at = now + timedelta(seconds=settings.AI_RETRY_SECONDS * job.attempt_count)
        if attempt is not None:
            attempt.retry_after = job.available_at
    else:
        job.status = JobStatus.FAILED
        job.completed_at = now
        audit.record('ai.job_failed', entity=job, actor=None, reason=message, job=job,
                     changes={'attempts': job.attempt_count, 'retryable': retryable})
    job.save(update_fields=['status', 'available_at', 'last_error', 'completed_at'])


def recover_stale(now=None):
    """Mark runs without a recent heartbeat as abandoned and retry their jobs."""
    now = now or timezone.now()
    cutoff = now - timedelta(seconds=settings.AI_JOB_STALE_SECONDS)
    recovered = 0
    stale = AIJobAttempt.objects.filter(status=AttemptStatus.RUNNING, heartbeat_at__lt=cutoff)
    for attempt_id in list(stale.values_list('pk', flat=True)):
        with transaction.atomic():
            attempt = AIJobAttempt.objects.select_for_update().get(pk=attempt_id)
            if attempt.status != AttemptStatus.RUNNING:
                continue
            job = AIJob.objects.select_for_update().get(pk=attempt.job_id)
            attempt.status = AttemptStatus.ABANDONED
            attempt.finished_at = now
            attempt.error_code = 'worker_stopped'
            attempt.error_message = 'No heartbeat from the worker; the run was abandoned.'
            _schedule_or_fail(job, attempt.error_message, retryable=True, attempt=attempt)
            attempt.save()
            recovered += 1
    return recovered


def claim_next(worker=None):
    """Take the next due job (skipping rows another worker holds)."""
    now = timezone.now()
    with transaction.atomic():
        job = (AIJob.objects.select_for_update(skip_locked=True)
               .filter(job_type__in=WORKER_JOB_TYPES, status=JobStatus.QUEUED,
                       invalidated_at__isnull=True, available_at__lte=now)
               .order_by('available_at', 'queued_at').first())
        if job is None:
            return None
        job.status = JobStatus.PROCESSING
        job.attempt_count += 1
        job.started_at = job.started_at or now
        job.save(update_fields=['status', 'attempt_count', 'started_at'])
        return AIJobAttempt.objects.create(
            job=job, attempt_number=job.attempts.count() + 1,
            worker_reference=worker or worker_reference(), started_at=now, heartbeat_at=now)


def heartbeat(attempt):
    AIJobAttempt.objects.filter(pk=attempt.pk, status=AttemptStatus.RUNNING) \
        .update(heartbeat_at=timezone.now(), updated_at=timezone.now())


def _fail(attempt, code, message, *, retryable):
    with transaction.atomic():
        attempt = AIJobAttempt.objects.select_for_update().get(pk=attempt.pk)
        if attempt.status != AttemptStatus.RUNNING:
            return
        job = AIJob.objects.select_for_update().get(pk=attempt.job_id)
        attempt.status = AttemptStatus.FAILED
        attempt.finished_at = timezone.now()
        attempt.error_code = code
        attempt.error_message = message[:2000]
        _schedule_or_fail(job, message, retryable=retryable, attempt=attempt)
        attempt.save()


def _warnings(pages, warnings):
    found = list(warnings)
    for page in pages:
        if page.get('page') is None and page.get('note'):
            found.append(page['note'])
        elif page.get('characters', 0) == 0:
            found.append(f'Page {page["page"]}: no text could be read.')
        elif page.get('ocr_confidence') is not None and page['ocr_confidence'] < LOW_OCR_CONFIDENCE:
            found.append(f'Page {page["page"]}: low OCR confidence '
                         f'({page["ocr_confidence"]:.0%}); check the text against the file.')
    return found


def _store(attempt, pages, items, warnings, tool_versions):
    with transaction.atomic():
        attempt = AIJobAttempt.objects.select_for_update().get(pk=attempt.pk)
        job = AIJob.objects.select_for_update().get(pk=attempt.job_id)
        version = DocumentVersion.objects.select_for_update().select_related(
            'document__application', 'document__document_type').get(pk=job.document_version_id)
        if attempt.status != AttemptStatus.RUNNING:
            return None  # recovered as abandoned meanwhile; another run owns the job
        problems = eligibility_problems(version)
        if job.invalidated_at or problems:
            if not job.invalidated_at:
                _invalidate(AIJob.objects.filter(pk=job.pk),
                            'Inputs changed during processing: ' + ' '.join(problems))
                job.refresh_from_db()
            attempt.status = AttemptStatus.FAILED
            attempt.finished_at = timezone.now()
            attempt.error_code = 'invalidated'
            attempt.error_message = 'The inputs changed during processing; results were discarded.'
            attempt.save()
            job.status = JobStatus.FAILED
            job.completed_at = timezone.now()
            job.last_error = attempt.error_message
            job.save(update_fields=['status', 'completed_at', 'last_error'])
            return None
        all_warnings = _warnings(pages, warnings)
        extraction = AIExtraction.objects.create(
            job=job, document_version=version, parser_version=parsers.PARSER_VERSION,
            engine_versions=tool_versions, pages=pages, warnings=all_warnings)
        for found in items:
            ExtractionItem.objects.create(extraction=extraction, **found)
        job.status = JobStatus.NEEDS_REVIEW if (all_warnings or not items) else JobStatus.COMPLETED
        job.completed_at = timezone.now()
        job.last_error = ''
        job.save(update_fields=['status', 'completed_at', 'last_error'])
        attempt.status = AttemptStatus.SUCCEEDED
        attempt.finished_at = timezone.now()
        attempt.save()
        audit.record('ai.extraction_completed', entity=job, actor=None, job=job,
                     changes={'application': version.document.application.reference,
                              'document_type': version.document.document_type.code,
                              'version': version.version, 'pages': len(pages),
                              'items': len(items), 'status': job.status,
                              'warnings': all_warnings})
        return extraction


def process(attempt):
    """Run one claimed attempt to completion (success, retry, or failure)."""
    job_type = AIJob.objects.values_list('job_type', flat=True).get(pk=attempt.job_id)
    if job_type == JobType.COMPETENCY_MATCH:
        from . import matching
        return matching.process(attempt, AIJob.objects.select_related('application')
                                .get(pk=attempt.job_id))
    job = AIJob.objects.select_related('document_version__document__document_type',
                                       'document_version__document__application').get(
        pk=attempt.job_id)
    version = job.document_version
    try:
        problems = eligibility_problems(version)
        if job.invalidated_at or problems:
            raise engine.ExtractionError('The document is no longer eligible: '
                                         + (' '.join(problems) or job.invalidation_reason))
        path = storage.path_for(version.storage_key)
        if not path.exists():
            raise engine.ExtractionError('The stored file is missing.')
        if _file_sha256(path) != version.sha256:
            raise engine.ExtractionError('The stored file does not match its recorded checksum.')
        pages = engine.extract_pages(path, version.mime_type, job.configuration,
                                     heartbeat=lambda: heartbeat(attempt))
        items, warnings = parsers.propose(version.document.document_type.code, pages,
                                          engine.load_nlp())
        tool_versions = engine.versions()
    except engine.EngineUnavailable as exc:
        _fail(attempt, 'runtime_missing', str(exc), retryable=True)
    except engine.ExtractionError as exc:
        _fail(attempt, 'unreadable', str(exc), retryable=False)
    except Exception as exc:  # unexpected: record it and retry
        _fail(attempt, 'error', f'{type(exc).__name__}: {exc}', retryable=True)
    else:
        return _store(attempt, pages, items, warnings, tool_versions)
    return None


def run_once(worker=None):
    """Recover stale runs, then process one due job. Returns the attempt or None."""
    recover_stale()
    attempt = claim_next(worker)
    if attempt is not None:
        process(attempt)
    return attempt


@transaction.atomic
def retry(actor, job):
    """HR retry of a failed job (same inputs, fresh attempt budget)."""
    job = AIJob.objects.select_for_update().select_related(
        'document_version__document__application', 'document_version__document__document_type'
    ).get(pk=job.pk)
    if not access.can_review(actor, job.application):
        raise PermissionDenied('You are not assigned to review this vacancy.')
    if job.invalidated_at:
        raise ValidationError('This job was invalidated; start a new run instead.')
    if job.status != JobStatus.FAILED:
        raise ValidationError('Only failed jobs can be retried.')
    if job.job_type == JobType.COMPETENCY_MATCH:
        from . import matching
        problems = matching.problems(job.application)
    else:
        problems = eligibility_problems(job.document_version)
    if problems:
        raise ValidationError(problems)
    job.status = JobStatus.QUEUED
    job.attempt_count = 0
    job.available_at = timezone.now()
    job.completed_at = None
    job.last_error = ''
    job.save(update_fields=['status', 'attempt_count', 'available_at', 'completed_at',
                            'last_error'])
    audit.record('ai.job_retried', entity=job, actor=actor, job=job,
                 changes={'previous_attempts': job.attempts.count()})
    return job


# --------------------------------------------------------------------------
# HR review of extracted items (13.3, FR-024)
# --------------------------------------------------------------------------

def schema_value(item_type, value):
    """Only the fact fields of this type, in schema order."""
    return {name: (value or {}).get(name) for name, *_ in fact_schema.FACT_FIELDS[item_type]}


def _normalize(text):
    return ''.join(ch for ch in str(text or '').lower() if ch.isalnum())


def matching_facts(item, facts):
    """[(fact, same, differing_fields)] for current facts that look like the
    same qualification, so HR can link instead of counting it twice."""
    proposed = {k: v for k, v in schema_value(item.item_type, item.proposed_value).items()
                if v not in (None, '')}
    matches = []
    for fact in facts:
        if fact.fact_type != item.item_type or not fact.is_current \
                or fact.review_status == FactReviewStatus.REJECTED:
            continue
        if not any(_normalize(fact.value.get(k)) and
                   _normalize(fact.value.get(k)) == _normalize(proposed.get(k))
                   for k in MATCH_KEYS.get(item.item_type, ())):
            continue
        differing = [k for k, v in proposed.items() if _normalize(fact.value.get(k)) != _normalize(v)]
        matches.append((fact, not differing, differing))
    return matches


def _reviewable(actor, item):
    item = ExtractionItem.objects.select_related('extraction__job__application').get(pk=item.pk)
    application = applications._lock(item.extraction.job.application)
    applications._require_reviewer(actor, application)
    item = ExtractionItem.objects.select_for_update().select_related(
        'extraction__job', 'extraction__document_version').get(pk=item.pk)
    job = item.extraction.job
    if job.invalidated_at:
        raise ValidationError(f'This extraction was invalidated ({job.invalidation_reason}) '
                              'and its items can no longer be used.')
    if item.review_status != ItemReviewStatus.PENDING:
        raise ValidationError('This item was already reviewed.')
    if item.item_type not in REVIEWABLE_TYPES:
        raise ValidationError('This item type cannot become an application fact.')
    return application, item, job


def _source_location(item):
    location = dict(item.source_location or {})
    page = item.page_number
    location['note'] = f'page {page}, AI extraction' if page else 'AI extraction'
    return location


def _finish_item(item, status, actor, reason, fact):
    item.review_status = status
    item.reviewed_by = actor
    item.reviewed_at = timezone.now()
    item.review_reason = reason
    item.resulting_fact = fact
    item.save(update_fields=['review_status', 'reviewed_by', 'reviewed_at', 'review_reason',
                             'resulting_fact'])


@transaction.atomic
def accept_item(actor, item, *, value=None, reason=''):
    """Accept a proposed item as a reviewed fact (as proposed, or corrected
    with a reason). The proposal itself stays unchanged."""
    application, item, job = _reviewable(actor, item)
    proposed = schema_value(item.item_type, item.proposed_value)
    final = schema_value(item.item_type, value) if value is not None else proposed
    differences = audit.diff(proposed, final)
    if differences and not reason:
        raise ValidationError('Explain the correction.')
    labels = {name: label for name, label, *_ in fact_schema.FACT_FIELDS[item.item_type]}
    missing = [labels[f] for f in fact_schema.REQUIRED_FIELDS.get(item.item_type, ())
               if final.get(f) in (None, '')]
    if missing:
        raise ValidationError('Fill in the required fields first (use Edit): '
                              + ', '.join(missing) + '.')
    fact = ApplicationFact.objects.create(
        application=application, fact_type=item.item_type,
        value={**final, 'extraction_item_id': str(item.pk)},
        review_status=FactReviewStatus.ACCEPTED,
        relevance_status=(RelevanceStatus.NOT_ASSESSED
                          if item.item_type in fact_schema.RELEVANCE_TYPES
                          else RelevanceStatus.NOT_APPLICABLE),
        reviewed_by=actor, reviewed_at=timezone.now(),
        review_reason=reason or 'Accepted from AI extraction')
    FactEvidence.objects.create(application_fact=fact,
                                document_version=item.extraction.document_version,
                                extraction_item=item, source_location=_source_location(item))
    _finish_item(item, ItemReviewStatus.CORRECTED if differences else ItemReviewStatus.ACCEPTED,
                 actor, reason, fact)
    applications._bump_revision(application)
    audit.record('ai.item_corrected' if differences else 'ai.item_accepted', entity=fact,
                 actor=actor, reason=reason, job=job,
                 changes={'item': str(item.pk), 'fact_type': item.item_type,
                          'proposed': proposed, 'accepted': final, 'differences': differences})
    return fact


@transaction.atomic
def link_item(actor, item, fact, *, reason=''):
    """Use the item as evidence for an existing fact instead of adding a second
    fact for the same qualification. The fact's value is not changed."""
    application, item, job = _reviewable(actor, item)
    fact = ApplicationFact.objects.select_for_update().get(pk=fact.pk)
    if fact.application_id != application.pk or not fact.is_current \
            or fact.fact_type != item.item_type:
        raise ValidationError('Choose a current fact of the same type from this application.')
    link, created = FactEvidence.objects.get_or_create(
        application_fact=fact, document_version=item.extraction.document_version,
        defaults={'extraction_item': item, 'source_location': _source_location(item)})
    if not created and link.extraction_item_id is None:
        link.extraction_item = item
        link.save(update_fields=['extraction_item'])
    _finish_item(item, ItemReviewStatus.LINKED, actor, reason, fact)
    differences = audit.diff(schema_value(item.item_type, fact.value),
                             {k: v for k, v in schema_value(item.item_type,
                                                            item.proposed_value).items()
                              if v not in (None, '')})
    audit.record('ai.item_linked', entity=fact, actor=actor, reason=reason, job=job,
                 changes={'item': str(item.pk), 'unchanged_fact_differs_in': differences})
    return fact


@transaction.atomic
def reject_item(actor, item, *, reason):
    if not (reason or '').strip():
        raise ValidationError('A reason is required to reject an item.')
    application, item, job = _reviewable(actor, item)
    _finish_item(item, ItemReviewStatus.REJECTED, actor, reason, None)
    audit.record('ai.item_rejected', entity=item, actor=actor, reason=reason, job=job,
                 changes={'item_type': item.item_type, 'proposed': item.proposed_value})
    return item


# --------------------------------------------------------------------------
# Read helpers for pages
# --------------------------------------------------------------------------

def jobs_by_version(application):
    """{document_version_id: job} with the latest job of each version."""
    found = {}
    for job in application.ai_jobs.filter(job_type=JobType.DOCUMENT_EXTRACTION)             .order_by('queued_at'):
        found[job.document_version_id] = job
    return found
