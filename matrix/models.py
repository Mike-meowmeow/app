"""Official applicant roster (FR-021 to FR-023), ERD Batch D.

A roster is a descriptive list of every submitted application for one vacancy
up to a cutoff. It has no scores, weights, or ranks; sequence numbers are only
display positions. Approved content and generated files never change: later
changes produce a new roster version.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from core.models import AppendOnlyModel, BaseModel


class TemplateStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    APPROVED = 'approved', 'Approved'
    RETIRED = 'retired', 'Retired'


class RosterTemplate(BaseModel):
    """An approved copy of an official roster form for one recruitment type.
    The file is stored privately and checked against its SHA-256 before use."""

    recruitment_type = models.ForeignKey('core.RecruitmentType', on_delete=models.PROTECT,
                                         related_name='roster_templates')
    version = models.PositiveIntegerField()
    name = models.CharField(max_length=200)
    storage_key = models.CharField(max_length=255)
    sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=20, choices=TemplateStatus.choices,
                              default=TemplateStatus.DRAFT)
    signatory_defaults = models.JSONField(default=dict, blank=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'roster_templates'
        ordering = ['recruitment_type', '-version']
        constraints = [
            models.UniqueConstraint(fields=['recruitment_type', 'version'],
                                    name='roster_template_unique_version'),
            models.CheckConstraint(condition=Q(version__gt=0),
                                   name='roster_template_version_positive'),
        ]

    def __str__(self):
        return f'{self.name} v{self.version} ({self.get_status_display()})'


class RosterStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    PENDING_APPROVAL = 'pending_approval', 'Pending Approval'
    RETURNED = 'returned', 'Returned for Correction'
    APPROVED = 'approved', 'Approved'
    SUPERSEDED = 'superseded', 'Superseded'


class ApplicantRoster(BaseModel):
    vacancy = models.ForeignKey('recruitment.Vacancy', on_delete=models.PROTECT,
                                related_name='rosters')
    template = models.ForeignKey(RosterTemplate, on_delete=models.PROTECT, related_name='rosters')
    version = models.PositiveIntegerField()
    cutoff_at = models.DateTimeField()
    generated_at = models.DateTimeField(default=timezone.now,
                                        help_text='When the entries were last built.')
    source_revision_manifest = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=RosterStatus.choices,
                              default=RosterStatus.DRAFT)
    requires_regeneration = models.BooleanField(default=False)
    prepared_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    related_name='+')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    approved_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    released_at = models.DateTimeField(null=True, blank=True)
    header_snapshot = models.JSONField(default=dict)
    signatory_snapshot = models.JSONField(default=dict)

    class Meta:
        db_table = 'applicant_rosters'
        ordering = ['vacancy', '-version']
        indexes = [models.Index(fields=['vacancy', 'status'], name='roster_vacancy_status_idx')]
        constraints = [
            models.UniqueConstraint(fields=['vacancy', 'version'], name='roster_unique_version'),
            models.CheckConstraint(condition=Q(version__gt=0), name='roster_version_positive'),
            models.CheckConstraint(
                condition=~Q(status='approved') | Q(approved_by__isnull=False,
                                                    approved_at__isnull=False),
                name='roster_approved_has_approver'),
        ]

    def __str__(self):
        return f'Roster v{self.version} - {self.vacancy}'

    @property
    def is_editable(self):
        return self.status in (RosterStatus.DRAFT, RosterStatus.RETURNED)

    @property
    def is_locked(self):
        return self.status in (RosterStatus.APPROVED, RosterStatus.SUPERSEDED)


class RosterEntry(BaseModel):
    """One application's exact row values (columns A:T as text) plus review
    flags. Rows of approved or superseded rosters cannot be changed."""

    roster = models.ForeignKey(ApplicantRoster, on_delete=models.PROTECT, related_name='entries')
    application = models.ForeignKey('recruitment.Application', on_delete=models.PROTECT,
                                    related_name='roster_entries')
    source_snapshot = models.ForeignKey('recruitment.ApplicationSnapshot',
                                        on_delete=models.PROTECT, related_name='+')
    sequence_number = models.PositiveIntegerField()
    exported_values = models.JSONField()
    review_flags = models.JSONField(default=dict)
    remarks = models.TextField(blank=True)

    class Meta:
        db_table = 'applicant_roster_entries'
        ordering = ['roster', 'sequence_number']
        constraints = [
            models.UniqueConstraint(fields=['roster', 'application'],
                                    name='roster_entry_unique_application'),
            models.UniqueConstraint(fields=['roster', 'sequence_number'],
                                    name='roster_entry_unique_sequence'),
            models.CheckConstraint(condition=Q(sequence_number__gt=0),
                                   name='roster_entry_sequence_positive'),
        ]

    def _guard(self):
        status = ApplicantRoster.objects.filter(pk=self.roster_id) \
            .values_list('status', flat=True).first()
        if status in (RosterStatus.APPROVED, RosterStatus.SUPERSEDED):
            raise PermissionError('Entries of an approved roster cannot be changed.')

    def save(self, *args, **kwargs):
        self._guard()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._guard()
        return super().delete(*args, **kwargs)

    @property
    def flagged_columns(self):
        return self.review_flags.get('fields', {})

    @property
    def needs_acknowledgment(self):
        """True when a value is missing/unverified or a file is unverified and
        HR has not acknowledged it. Not-applicable values need no action."""
        flags = self.review_flags
        has_flags = any(flag != 'not_applicable' for flag in flags.get('fields', {}).values()) \
            or bool(flags.get('documents'))
        return has_flags and not flags.get('acknowledged')


class RosterApprovalEvent(AppendOnlyModel):
    roster = models.ForeignKey(ApplicantRoster, on_delete=models.PROTECT, related_name='events')
    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20, choices=RosterStatus.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              null=True, blank=True, related_name='+')
    comments = models.TextField(blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'roster_approval_events'
        ordering = ['occurred_at']


class ExportFormat(models.TextChoices):
    XLSX = 'xlsx', 'Excel'
    PDF = 'pdf', 'PDF'


class RosterExport(AppendOnlyModel):
    """A generated file. Files are written once and never overwritten."""

    roster = models.ForeignKey(ApplicantRoster, on_delete=models.PROTECT, related_name='exports')
    format = models.CharField(max_length=10, choices=ExportFormat.choices)
    is_draft = models.BooleanField(default=True)
    storage_key = models.CharField(max_length=255, unique=True)
    sha256 = models.CharField(max_length=64)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     related_name='+')
    generated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'roster_exports'
        ordering = ['-generated_at']

    @property
    def file_name(self):
        roster = self.roster
        kind = 'DRAFT' if self.is_draft else 'OFFICIAL'
        stamp = timezone.localtime(self.generated_at).strftime('%Y%m%d-%H%M')
        return f'Roster-{roster.vacancy.reference}-v{roster.version}-{kind}-{stamp}.{self.format}'
