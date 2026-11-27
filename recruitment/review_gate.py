"""When the qualification review opens (28 November 2026, user decision).

The qualification review (recording outcomes, the advisory qualification
check, and competency matching) opens only when:

1. every document for a required checklist item has a decision on its latest
   version (Verified, Incomplete, Invalid, or Rejected); optional uploads do
   not hold it back;
2. every fact is decided (accepted or rejected); and
3. with AI extraction on, no extraction job for the current documents is still
   queued or processing. HR can skip a stuck job with "Continue without AI"
   and a reason (ai_processing.services.skip_running_extraction). With AI off
   only 1 and 2 count.

The official roster and shortlisting do not depend on this gate.
"""

from django.conf import settings

from documents.models import VerificationStatus

from .models import FactReviewStatus, FactType

RUNNING = ('queued', 'processing')


def _required_documents(application):
    from . import application_services as services
    found = []
    for requirement, docs in services.document_checklist(application):
        if requirement.is_required:
            found += docs
    return found


def running_extraction(application):
    """Live extraction jobs (queued or processing) on current document versions."""
    if not settings.AI_EXTRACTION_ENABLED:
        return []
    from ai_processing.models import JobType
    jobs = application.ai_jobs.filter(job_type=JobType.DOCUMENT_EXTRACTION,
                                      invalidated_at__isnull=True, status__in=RUNNING) \
        .select_related('document_version__document__document_type')
    return [j for j in jobs
            if j.document_version.document.current_version().pk == j.document_version_id]


def status(application):
    """{'ready': bool, 'items': [(done, text)], 'running': [jobs]} for the
    "what's missing" checklist on the review page."""
    items = []

    docs = _required_documents(application)
    pending, unusable = [], []
    for doc in docs:
        version = doc.current_version()
        if version is None:
            continue
        label = f'{doc.document_type.name} v{version.version}'
        if not version.is_usable:
            unusable.append(label)
        elif version.verification_status == VerificationStatus.PENDING:
            pending.append(label)
    decided = len(docs) - len(pending) - len(unusable)
    text = f'Required documents decided: {decided} of {len(docs)}'
    if pending:
        text += f' (waiting: {", ".join(pending)})'
    if unusable:
        text += f' (failed file checks, request a replacement: {", ".join(unusable)})'
    items.append((not pending and not unusable, text))

    facts = [f for f in application.facts.exclude(review_status=FactReviewStatus.SUPERSEDED)
             .exclude(fact_type=FactType.QUALIFICATION_ASSESSMENT)]
    waiting = [f for f in facts if f.review_status == FactReviewStatus.PENDING]
    items.append((not waiting, f'Facts decided (accepted or rejected): '
                               f'{len(facts) - len(waiting)} of {len(facts)}'))

    running = running_extraction(application)
    if settings.AI_EXTRACTION_ENABLED:
        if running:
            names = ', '.join(f'{j.document_version.document.document_type.name} '
                              f'v{j.document_version.version}' for j in running)
            items.append((False, f'AI extraction still running: {names}'))
        else:
            items.append((True, 'AI extraction finished'))

    return {'ready': all(done for done, _ in items), 'items': items, 'running': running}


def problems(application):
    """[] when the qualification review is open, else what is missing."""
    found = status(application)
    if found['ready']:
        return []
    return ['The qualification review opens when the documents are decided, the facts are '
            'reviewed, and extraction is finished. Missing: '
            + '; '.join(text for done, text in found['items'] if not done) + '.']


def documents_by_standard(application, standards):
    """Short links under each standard (step 3): the latest version of each
    document whose type relates to the standard's criterion (the same mapping
    as profile attachments). 'Other' standards list every document. Returns
    ({standard_id: [versions]}, [supporting versions]) where supporting are the
    documents of types no criterion uses (PDS, resume, letter, other)."""
    from applicants.models import SECTION_DOCUMENT_TYPES
    current = []
    for doc in application.documents.select_related('document_type').order_by(
            'document_type__name', 'created_at'):
        version = doc.current_version()
        if version is not None:
            current.append(version)
    mapped = {code for codes in SECTION_DOCUMENT_TYPES.values() for code in codes}
    by_standard = {}
    for standard in standards:
        codes = SECTION_DOCUMENT_TYPES.get(standard.criterion_type)
        by_standard[str(standard.pk)] = [
            v for v in current if codes is None or v.document.document_type.code in codes]
    supporting = [v for v in current if v.document.document_type.code not in mapped]
    return by_standard, supporting
