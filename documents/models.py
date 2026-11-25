from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import AppendOnlyModel, BaseModel

DEFAULT_MIME_TYPES = ['application/pdf', 'image/jpeg', 'image/png']
DEFAULT_MAX_SIZE = 10 * 1024 * 1024


def default_mime_types():
    return list(DEFAULT_MIME_TYPES)


class DocumentType(BaseModel):
    """Configurable document category (PDS, transcript, certificate, ...).
    Deactivate instead of deleting once referenced."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=150)
    allowed_mime_types = models.JSONField(default=default_mime_types)
    max_size_bytes = models.PositiveBigIntegerField(default=DEFAULT_MAX_SIZE)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'document_types'
        ordering = ['name']
        constraints = [
            models.CheckConstraint(condition=Q(max_size_bytes__gt=0),
                                   name='document_type_max_size_positive'),
        ]

    def __str__(self):
        return self.name

    @property
    def max_size_mb(self):
        return round(self.max_size_bytes / (1024 * 1024), 1)

    @property
    def allowed_extensions(self):
        from .storage import EXTENSIONS
        return sorted({ext for mime in self.allowed_mime_types for ext in EXTENSIONS.get(mime, ())})


# ==========================================================================
# Application documents (Stage 5, Batch C)
# ==========================================================================

class ValidationStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    VALID = 'valid', 'Valid'
    INVALID = 'invalid', 'Invalid'


class MalwareStatus(models.TextChoices):
    NOT_SCANNED = 'not_scanned', 'Not scanned'
    CLEAN = 'clean', 'Clean'
    QUARANTINED = 'quarantined', 'Quarantined'


class VerificationStatus(models.TextChoices):
    PENDING = 'pending', 'Pending Verification'
    VERIFIED = 'verified', 'Verified'
    INCOMPLETE = 'incomplete', 'Incomplete'
    INVALID = 'invalid', 'Invalid'
    REJECTED = 'rejected', 'Rejected'


class Document(BaseModel):
    """A logical document of one application (e.g. its TOR). Each upload or
    replacement adds a DocumentVersion; earlier versions are kept."""

    application = models.ForeignKey('recruitment.Application', on_delete=models.PROTECT,
                                    related_name='documents')
    document_type = models.ForeignKey(DocumentType, on_delete=models.PROTECT,
                                      related_name='documents')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name='+')
    uploaded_at = models.DateTimeField(default=timezone.now)
    # Set when the file was copied from a file attached to the applicant's
    # profile record; the application keeps its own copy of the file.
    profile_document = models.ForeignKey('applicants.ProfileDocument', on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='application_copies')

    class Meta:
        db_table = 'documents'
        ordering = ['document_type__name', 'uploaded_at']

    def __str__(self):
        return f'{self.document_type.name} ({self.application.reference})'

    def current_version(self):
        return self.versions.order_by('-version').first()


IMMUTABLE_VERSION_FIELDS = {'document', 'version', 'correction_request', 'file_name',
                            'storage_key', 'file_size_bytes', 'mime_type', 'sha256',
                            'validation_status', 'uploaded_by', 'uploaded_at'}


class DocumentVersion(BaseModel):
    """One stored file. File metadata never changes; verification_status is the
    current projection of the DocumentVerification history."""

    document = models.ForeignKey(Document, on_delete=models.PROTECT, related_name='versions')
    version = models.PositiveIntegerField()
    correction_request = models.ForeignKey('recruitment.CorrectionRequest',
                                           on_delete=models.PROTECT, null=True, blank=True,
                                           related_name='document_versions')
    file_name = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=255, unique=True)
    file_size_bytes = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    validation_status = models.CharField(max_length=20, choices=ValidationStatus.choices,
                                         default=ValidationStatus.PENDING)
    malware_status = models.CharField(max_length=20, choices=MalwareStatus.choices,
                                      default=MalwareStatus.NOT_SCANNED)
    verification_status = models.CharField(max_length=20, choices=VerificationStatus.choices,
                                           default=VerificationStatus.PENDING)
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name='+')
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'document_versions'
        ordering = ['document', '-version']
        indexes = [models.Index(fields=['verification_status'],
                                name='document_version_verify_idx')]
        constraints = [
            models.UniqueConstraint(fields=['document', 'version'],
                                    name='document_version_unique'),
            models.CheckConstraint(condition=Q(version__gt=0),
                                   name='document_version_positive'),
        ]

    def __str__(self):
        return f'{self.file_name} v{self.version}'

    @property
    def is_usable(self):
        """Technically valid and not quarantined."""
        return (self.validation_status == ValidationStatus.VALID
                and self.malware_status != MalwareStatus.QUARANTINED)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            fields = kwargs.get('update_fields')
            if fields is None or set(fields) & IMMUTABLE_VERSION_FIELDS:
                raise PermissionError('Stored file versions cannot be modified; only their '
                                      'verification or scan status can change.')
        super().save(*args, **kwargs)


class DocumentVerification(AppendOnlyModel):
    """Human verification event for one exact file version (history kept)."""

    document_version = models.ForeignKey(DocumentVersion, on_delete=models.PROTECT,
                                         related_name='verifications')
    status = models.CharField(max_length=20, choices=VerificationStatus.choices)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name='+')
    reviewed_at = models.DateTimeField(default=timezone.now)
    notes = models.TextField(blank=True)

    class Meta:
        db_table = 'document_verifications'
        ordering = ['reviewed_at']


class FactEvidence(BaseModel):
    """Links an application fact to the exact file version supporting it, and
    (Stage 10, optional) to the AI extraction item HR reviewed. Manual evidence
    keeps extraction_item null."""

    application_fact = models.ForeignKey('recruitment.ApplicationFact', on_delete=models.PROTECT,
                                         related_name='evidence')
    document_version = models.ForeignKey(DocumentVersion, on_delete=models.PROTECT,
                                         related_name='evidence')
    extraction_item = models.ForeignKey('ai_processing.ExtractionItem', on_delete=models.PROTECT,
                                        null=True, blank=True, related_name='evidence')
    source_location = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = 'fact_evidence'
        constraints = [
            models.UniqueConstraint(fields=['application_fact', 'document_version'],
                                    name='fact_evidence_unique'),
        ]
