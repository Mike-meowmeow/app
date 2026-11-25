"""Stage 10 AI processing records (FR-008, FR-024).

A job processes one verified document version. Each run is an attempt; a
successful run stores one extraction (page text) and its proposed items, which
HR accepts, corrects, links, or rejects. Nothing here changes application
facts by itself: accepted items become ordinary reviewed facts through the
review services, and the original extraction is never edited.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import BaseModel


class JobType(models.TextChoices):
    DOCUMENT_EXTRACTION = 'document_extraction', 'Document text extraction'
    QUALIFICATION_CHECK = 'qualification_check', 'Qualification check'


class JobStatus(models.TextChoices):
    QUEUED = 'queued', 'Queued'
    PROCESSING = 'processing', 'Processing'
    COMPLETED = 'completed', 'Completed'
    NEEDS_REVIEW = 'needs_review', 'Needs Review'
    FAILED = 'failed', 'Failed'


class AIJob(BaseModel):
    application = models.ForeignKey('recruitment.Application', on_delete=models.PROTECT,
                                    related_name='ai_jobs')
    document_version = models.ForeignKey('documents.DocumentVersion', on_delete=models.PROTECT,
                                         null=True, blank=True, related_name='ai_jobs')
    input_snapshot = models.ForeignKey('recruitment.ApplicationSnapshot',
                                       on_delete=models.PROTECT, null=True, blank=True,
                                       related_name='ai_jobs')
    job_type = models.CharField(max_length=40, choices=JobType.choices)
    status = models.CharField(max_length=20, choices=JobStatus.choices,
                              default=JobStatus.QUEUED)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     null=True, blank=True, related_name='+')
    model_name = models.CharField(max_length=100)
    model_version = models.CharField(max_length=200)
    configuration = models.JSONField(default=dict, blank=True)
    input_manifest = models.JSONField(default=dict, blank=True)
    input_hash = models.CharField(max_length=64)
    deduplication_key = models.CharField(max_length=64, unique=True)
    # Runs left before the job is marked Failed; reset by an HR retry.
    attempt_count = models.PositiveSmallIntegerField(default=0)
    available_at = models.DateTimeField(default=timezone.now)
    last_error = models.TextField(blank=True)
    queued_at = models.DateTimeField(default=timezone.now)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidation_reason = models.TextField(blank=True)

    class Meta:
        db_table = 'ai_jobs'
        ordering = ['-queued_at']
        indexes = [models.Index(fields=['status', 'available_at'], name='ai_job_queue_idx')]

    def __str__(self):
        return f'{self.get_job_type_display()} {self.pk}'

    @property
    def is_invalidated(self):
        return self.invalidated_at is not None

    @property
    def is_active(self):
        return not self.is_invalidated and self.status in (JobStatus.QUEUED, JobStatus.PROCESSING)

    @property
    def display_status(self):
        return 'Invalidated' if self.is_invalidated else self.get_status_display()


class AttemptStatus(models.TextChoices):
    RUNNING = 'running', 'Running'
    SUCCEEDED = 'succeeded', 'Succeeded'
    FAILED = 'failed', 'Failed'
    ABANDONED = 'abandoned', 'Abandoned (worker stopped)'


class AIJobAttempt(BaseModel):
    job = models.ForeignKey(AIJob, on_delete=models.PROTECT, related_name='attempts')
    attempt_number = models.PositiveIntegerField()
    worker_reference = models.CharField(max_length=200)
    status = models.CharField(max_length=20, choices=AttemptStatus.choices,
                              default=AttemptStatus.RUNNING)
    started_at = models.DateTimeField(default=timezone.now)
    heartbeat_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    retry_after = models.DateTimeField(null=True, blank=True)
    error_code = models.CharField(max_length=50, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = 'ai_job_attempts'
        ordering = ['job', 'attempt_number']
        constraints = [models.UniqueConstraint(fields=['job', 'attempt_number'],
                                               name='ai_attempt_unique')]


class AIExtraction(BaseModel):
    """Text read from one document version: per page, how it was read (digital
    text or OCR), and the exact text. Kept unchanged after HR review."""

    job = models.OneToOneField(AIJob, on_delete=models.PROTECT, related_name='extraction')
    document_version = models.ForeignKey('documents.DocumentVersion', on_delete=models.PROTECT,
                                         related_name='ai_extractions')
    extracted_at = models.DateTimeField(default=timezone.now)
    parser_version = models.CharField(max_length=50)
    engine_versions = models.JSONField(default=dict, blank=True)
    pages = models.JSONField(default=list, blank=True)
    warnings = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = 'ai_extractions'


class ItemReviewStatus(models.TextChoices):
    PENDING = 'pending', 'Pending review'
    ACCEPTED = 'accepted', 'Accepted'
    CORRECTED = 'corrected', 'Accepted with corrections'
    LINKED = 'linked', 'Linked to an existing fact'
    REJECTED = 'rejected', 'Rejected'


class ExtractionItem(BaseModel):
    """One proposed qualification item. proposed_value is never edited; HR's
    decision is recorded beside it and, when accepted, as a separate fact."""

    extraction = models.ForeignKey(AIExtraction, on_delete=models.PROTECT, related_name='items')
    item_type = models.CharField(max_length=30)
    raw_text = models.TextField()
    proposed_value = models.JSONField(default=dict)
    page_number = models.PositiveIntegerField(null=True, blank=True)
    source_location = models.JSONField(default=dict, blank=True)
    confidence = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    review_status = models.CharField(max_length=20, choices=ItemReviewStatus.choices,
                                     default=ItemReviewStatus.PENDING)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.TextField(blank=True)
    resulting_fact = models.ForeignKey('recruitment.ApplicationFact', on_delete=models.PROTECT,
                                       null=True, blank=True, related_name='extraction_items')

    class Meta:
        db_table = 'extraction_items'
        ordering = ['extraction', 'page_number', 'created_at']
        constraints = [
            models.CheckConstraint(
                condition=Q(confidence__isnull=True) | (Q(confidence__gte=0) & Q(confidence__lte=1)),
                name='extraction_item_confidence_range'),
        ]

    def __str__(self):
        return f'{self.item_type} item {self.pk}'


class QualificationResult(models.TextChoices):
    """The four outcomes, the same as HR's manual qualification outcomes."""
    MEETS = 'meets', 'Meets Requirement'
    DOES_NOT_MEET = 'does_not_meet', 'Does Not Meet Requirement'
    NEEDS_REVIEW = 'needs_review', 'Needs Review'
    NOT_REQUIRED = 'not_required', 'Not Required'


class AIScreening(BaseModel):
    """One advisory qualification check (13.4): the application's accepted
    facts compared with its published requirement version by fixed rules.
    Advisory only; HR records the official outcome separately."""

    job = models.OneToOneField(AIJob, on_delete=models.PROTECT, related_name='screening')
    input_snapshot = models.ForeignKey('recruitment.ApplicationSnapshot',
                                       on_delete=models.PROTECT, related_name='ai_screenings')
    overall_status = models.CharField(max_length=20, choices=QualificationResult.choices)
    screened_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'ai_screenings'
        ordering = ['-screened_at']


class StandardResult(BaseModel):
    """The check's result for one qualification standard, with the facts it
    used and the reasoning, one step per line."""

    screening = models.ForeignKey(AIScreening, on_delete=models.PROTECT, related_name='results')
    standard = models.ForeignKey('recruitment.QualificationStandard', on_delete=models.PROTECT,
                                 related_name='ai_results')
    result = models.CharField(max_length=20, choices=QualificationResult.choices)
    evidence_fact_ids = models.JSONField(default=list, blank=True)
    explanation = models.TextField()

    class Meta:
        db_table = 'qualification_results'
        ordering = ['screening', 'created_at']
        constraints = [models.UniqueConstraint(fields=['screening', 'standard'],
                                               name='qualification_result_unique')]

    @property
    def explanation_lines(self):
        return [line for line in self.explanation.splitlines() if line.strip()]
