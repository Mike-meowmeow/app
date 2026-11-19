from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from core.models import AppendOnlyModel, BaseModel


class VacancyStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    PUBLISHED = 'published', 'Published'
    CLOSED = 'closed', 'Closed'
    FILLED = 'filled', 'Filled'
    CANCELED = 'canceled', 'Canceled'


class Vacancy(BaseModel):
    department = models.ForeignKey('core.Department', on_delete=models.PROTECT,
                                   related_name='vacancies')
    recruitment_type = models.ForeignKey('core.RecruitmentType', on_delete=models.PROTECT,
                                         related_name='vacancies')
    # Catalog position whose defaults were copied in. Null for vacancies created
    # before the catalog existed. position_title and description are the
    # vacancy's own copies and never follow later catalog edits.
    position = models.ForeignKey('Position', on_delete=models.PROTECT, null=True, blank=True,
                                 related_name='vacancies')
    position_title = models.CharField(max_length=200)
    description = models.TextField()
    positions_available = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=20, choices=VacancyStatus.choices,
                              default=VacancyStatus.DRAFT)
    opens_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    filled_at = models.DateTimeField(null=True, blank=True)
    canceled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'vacancies'
        ordering = ['-created_at']
        verbose_name_plural = 'vacancies'
        indexes = [models.Index(fields=['status', 'deadline_at'], name='vacancy_status_deadline_idx')]
        constraints = [
            models.CheckConstraint(condition=Q(positions_available__gt=0),
                                   name='vacancy_positions_positive'),
            models.CheckConstraint(
                condition=Q(opens_at__isnull=True) | Q(deadline_at__isnull=True)
                | Q(opens_at__lte=F('deadline_at')),
                name='vacancy_opens_before_deadline'),
        ]

    def __str__(self):
        return f'{self.position_title} ({self.reference})'

    @property
    def reference(self):
        return f'VAC-{self.pk.hex[:8].upper()}' if self.pk else 'VAC-NEW'

    def is_accepting_applications(self, now=None):
        now = now or timezone.now()
        return (self.status == VacancyStatus.PUBLISHED and self.opens_at is not None
                and self.deadline_at is not None and self.opens_at <= now < self.deadline_at)

    @property
    def application_window(self):
        """'open', 'upcoming', or 'ended' for published vacancies."""
        now = timezone.now()
        if self.opens_at and now < self.opens_at:
            return 'upcoming'
        if self.deadline_at and now >= self.deadline_at:
            return 'ended'
        return 'open'

    def current_requirements(self):
        return self.requirement_versions.filter(published_at__isnull=False) \
            .order_by('-version').first()

    def draft_requirements(self):
        return self.requirement_versions.filter(published_at__isnull=True).first()


class VacancyRequirementVersion(BaseModel):
    """A set of vacancy requirements. Drafts are editable; once published the
    version and its items are immutable. Child rows are authoritative; the JSON
    manifests preserve the exact published configuration."""

    vacancy = models.ForeignKey(Vacancy, on_delete=models.PROTECT,
                                related_name='requirement_versions')
    version = models.PositiveIntegerField()
    qualification_rules = models.JSONField(default=dict, blank=True)
    document_checklist = models.JSONField(default=dict, blank=True)
    competency_definitions = models.JSONField(default=dict, blank=True)
    published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     null=True, blank=True, related_name='+')

    class Meta:
        db_table = 'vacancy_requirement_versions'
        ordering = ['vacancy', '-version']
        constraints = [
            models.UniqueConstraint(fields=['vacancy', 'version'],
                                    name='requirement_version_unique'),
            models.CheckConstraint(condition=Q(version__gt=0),
                                   name='requirement_version_positive'),
        ]

    def __str__(self):
        return f'{self.vacancy.reference} v{self.version}'

    @property
    def is_published(self):
        return self.published_at is not None

    def save(self, *args, **kwargs):
        if not self._state.adding and type(self).objects.filter(
                pk=self.pk, published_at__isnull=False).exists():
            raise PermissionError('Published requirement versions cannot be changed.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if type(self).objects.filter(pk=self.pk, published_at__isnull=False).exists():
            raise PermissionError('Published requirement versions cannot be deleted.')
        return super().delete(*args, **kwargs)


class RequirementItem(BaseModel):
    """Items of a requirement version; frozen once the version is published."""

    class Meta:
        abstract = True

    def _guard(self):
        if VacancyRequirementVersion.objects.filter(
                pk=self.requirement_version_id, published_at__isnull=False).exists():
            raise PermissionError('Items of a published requirement version cannot change.')

    def save(self, *args, **kwargs):
        self._guard()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        self._guard()
        return super().delete(*args, **kwargs)


class VacancyDocumentRequirement(RequirementItem):
    requirement_version = models.ForeignKey(VacancyRequirementVersion,
                                            on_delete=models.CASCADE,
                                            related_name='document_requirements')
    document_type = models.ForeignKey('documents.DocumentType', on_delete=models.PROTECT,
                                      related_name='vacancy_requirements')
    is_required = models.BooleanField(default=True)
    max_files = models.PositiveSmallIntegerField(default=1)
    instructions = models.TextField(blank=True)

    class Meta:
        db_table = 'vacancy_document_requirements'
        ordering = ['-is_required', 'document_type__name']
        constraints = [
            models.UniqueConstraint(fields=['requirement_version', 'document_type'],
                                    name='vacancy_document_requirement_unique'),
            models.CheckConstraint(condition=Q(max_files__gt=0),
                                   name='vacancy_document_max_files_positive'),
        ]


class CriterionType(models.TextChoices):
    EDUCATION = 'education', 'Education'
    EXPERIENCE = 'experience', 'Work experience'
    TRAINING = 'training', 'Training'
    ELIGIBILITY = 'eligibility', 'Eligibility'
    LICENSE = 'license', 'Professional license'
    OTHER = 'other', 'Other'


class StandardOperator(models.TextChoices):
    MINIMUM = 'minimum', 'At least'
    REQUIRED = 'required', 'Must have'
    SPECIFIC = 'specific', 'Specific requirement'


class QualificationStandard(RequirementItem):
    """Structured requirement, e.g. education at least Master's in a field,
    or at least 2 years of teaching experience. No scoring weights."""

    requirement_version = models.ForeignKey(VacancyRequirementVersion,
                                            on_delete=models.CASCADE,
                                            related_name='qualification_standards')
    criterion_type = models.CharField(max_length=20, choices=CriterionType.choices)
    operator = models.CharField(max_length=20, choices=StandardOperator.choices)
    required_value = models.JSONField(default=dict, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    description = models.TextField()

    class Meta:
        db_table = 'qualification_standards'
        ordering = ['created_at']


class VacancyCompetency(RequirementItem):
    requirement_version = models.ForeignKey(VacancyRequirementVersion,
                                            on_delete=models.CASCADE,
                                            related_name='competencies')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    class Meta:
        db_table = 'vacancy_competencies'
        ordering = ['created_at']
        verbose_name_plural = 'vacancy competencies'


# --------------------------------------------------------------------------
# Position catalog: reusable defaults copied into new vacancies
# --------------------------------------------------------------------------

class Position(BaseModel):
    """An HR-managed position with default description and requirements.
    Defaults are copied into a vacancy when it is created; the vacancy then
    keeps its own copy. Positions are deactivated, never deleted, once used."""

    title = models.CharField(max_length=200, unique=True)
    description = models.TextField()
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'positions'
        ordering = ['title']

    def __str__(self):
        return self.title if self.is_active else f'{self.title} (inactive)'


class PositionDocumentRequirement(BaseModel):
    position = models.ForeignKey(Position, on_delete=models.CASCADE,
                                 related_name='document_requirements')
    document_type = models.ForeignKey('documents.DocumentType', on_delete=models.PROTECT,
                                      related_name='position_requirements')
    is_required = models.BooleanField(default=True)
    max_files = models.PositiveSmallIntegerField(default=1)
    instructions = models.TextField(blank=True)

    class Meta:
        db_table = 'position_document_requirements'
        ordering = ['-is_required', 'document_type__name']
        constraints = [
            models.UniqueConstraint(fields=['position', 'document_type'],
                                    name='position_document_requirement_unique'),
            models.CheckConstraint(condition=Q(max_files__gt=0),
                                   name='position_document_max_files_positive'),
        ]


class PositionQualificationStandard(BaseModel):
    """Same structure as QualificationStandard, built by the same rules."""

    position = models.ForeignKey(Position, on_delete=models.CASCADE,
                                 related_name='qualification_standards')
    criterion_type = models.CharField(max_length=20, choices=CriterionType.choices)
    operator = models.CharField(max_length=20, choices=StandardOperator.choices)
    required_value = models.JSONField(default=dict, blank=True)
    unit = models.CharField(max_length=20, blank=True)
    description = models.TextField()

    class Meta:
        db_table = 'position_qualification_standards'
        ordering = ['created_at']


class PositionCompetency(BaseModel):
    position = models.ForeignKey(Position, on_delete=models.CASCADE,
                                 related_name='competencies')
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    class Meta:
        db_table = 'position_competencies'
        ordering = ['created_at']
        verbose_name_plural = 'position competencies'


class AssignmentRole(models.TextChoices):
    HR = 'hr', 'HR handler'
    DEPARTMENT_HEAD = 'department_head', 'Department Head'


class VacancyAssignment(BaseModel):
    """Explicit staff access to one vacancy. Ended assignments stay as history
    and are reactivated through an audited action."""

    vacancy = models.ForeignKey(Vacancy, on_delete=models.PROTECT, related_name='assignments')
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name='vacancy_assignments')
    assignment_role = models.CharField(max_length=20, choices=AssignmentRole.choices)
    assigned_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'vacancy_assignments'
        ordering = ['assignment_role', 'created_at']
        constraints = [
            models.UniqueConstraint(fields=['vacancy', 'user', 'assignment_role'],
                                    name='vacancy_assignment_unique'),
        ]

    @property
    def is_active(self):
        return self.ended_at is None


# ==========================================================================
# Applications (Stage 5, Batch C)
# ==========================================================================

class ApplicationStatus(models.TextChoices):
    DRAFT = 'draft', 'Draft'
    SUBMITTED = 'submitted', 'Submitted'
    UNDER_REVIEW = 'under_review', 'Under Review'
    CORRECTION_REQUESTED = 'correction_requested', 'Correction Requested'
    SHORTLISTED = 'shortlisted', 'Shortlisted'
    FOR_INTERVIEW = 'for_interview', 'For Interview'
    RECOMMENDED = 'recommended', 'Recommended'
    HIRED = 'hired', 'Hired'
    NOT_SELECTED = 'not_selected', 'Not Selected'
    WITHDRAWN = 'withdrawn', 'Withdrawn'
    CLOSED_WITHOUT_SELECTION = 'closed_without_selection', 'Closed Without Selection'


class Application(BaseModel):
    """One application per applicant and vacancy. `revision` increases on every
    roster-visible change. Submitted applications are never hard-deleted."""

    applicant = models.ForeignKey('applicants.Applicant', on_delete=models.PROTECT,
                                  related_name='applications')
    vacancy = models.ForeignKey(Vacancy, on_delete=models.PROTECT, related_name='applications')
    status = models.CharField(max_length=30, choices=ApplicationStatus.choices,
                              default=ApplicationStatus.DRAFT)
    submitted_at = models.DateTimeField(null=True, blank=True,
                                        help_text='First successful submission.')
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    revision = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = 'applications'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['vacancy', 'status', 'submitted_at'],
                                name='application_vacancy_status_idx')]
        constraints = [
            models.UniqueConstraint(fields=['applicant', 'vacancy'],
                                    name='application_unique_per_vacancy'),
        ]

    def __str__(self):
        return f'{self.reference} - {self.applicant}'

    @property
    def reference(self):
        return f'APP-{self.pk.hex[:8].upper()}' if self.pk else 'APP-NEW'

    def open_correction_request(self):
        return self.correction_requests.filter(status=CorrectionStatus.OPEN).first()

    def latest_snapshot(self):
        return self.snapshots.order_by('-version').first()


class SnapshotType(models.TextChoices):
    SUBMISSION = 'submission', 'Submission'
    CORRECTION = 'correction', 'Correction'
    REVIEWED = 'reviewed', 'Reviewed'
    ANALYSIS_INPUT = 'analysis_input', 'Analysis Input'


class ApplicationSnapshot(AppendOnlyModel):
    """Exact submitted information (schema-versioned JSON). Never changes."""

    application = models.ForeignKey(Application, on_delete=models.PROTECT,
                                    related_name='snapshots')
    requirement_version = models.ForeignKey(VacancyRequirementVersion, on_delete=models.PROTECT,
                                            related_name='+')
    version = models.PositiveIntegerField()
    snapshot_type = models.CharField(max_length=20, choices=SnapshotType.choices)
    data = models.JSONField()
    captured_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    captured_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'application_snapshots'
        ordering = ['application', 'version']
        constraints = [
            models.UniqueConstraint(fields=['application', 'version'],
                                    name='application_snapshot_unique'),
            models.CheckConstraint(condition=Q(version__gt=0),
                                   name='application_snapshot_version_positive'),
        ]


class FactType(models.TextChoices):
    IDENTITY = 'identity', 'Identity and contact'
    EDUCATION = 'education', 'Education'
    EXPERIENCE = 'experience', 'Work experience'
    TRAINING = 'training', 'Training'
    ELIGIBILITY = 'eligibility', 'Eligibility'
    LICENSE = 'license', 'Professional license'
    QUALIFICATION_ASSESSMENT = 'qualification_assessment', 'Qualification assessment'


class FactReviewStatus(models.TextChoices):
    PENDING = 'pending', 'Pending review'
    ACCEPTED = 'accepted', 'Accepted'
    REJECTED = 'rejected', 'Rejected'
    SUPERSEDED = 'superseded', 'Superseded'


class RelevanceStatus(models.TextChoices):
    NOT_ASSESSED = 'not_assessed', 'Not assessed'
    RELEVANT = 'relevant', 'Relevant'
    NOT_RELEVANT = 'not_relevant', 'Not relevant'
    NOT_APPLICABLE = 'not_applicable', 'Not applicable'


class ApplicationFact(BaseModel):
    """A typed item of application information under human review. Changing a
    value creates a new fact that supersedes the old one; the old one is kept.

    Manual qualification outcomes (Meets / Does Not Meet / Needs Review / Not
    Required) are stored as qualification_assessment facts, because the ERD
    qualification_results table belongs to the Stage 10 AI screening."""

    application = models.ForeignKey(Application, on_delete=models.PROTECT, related_name='facts')
    fact_type = models.CharField(max_length=30, choices=FactType.choices)
    value = models.JSONField()
    review_status = models.CharField(max_length=20, choices=FactReviewStatus.choices,
                                     default=FactReviewStatus.PENDING)
    relevance_status = models.CharField(max_length=20, choices=RelevanceStatus.choices,
                                        default=RelevanceStatus.NOT_APPLICABLE)
    source_snapshot = models.ForeignKey(ApplicationSnapshot, on_delete=models.PROTECT,
                                        null=True, blank=True, related_name='facts')
    supersedes = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True,
                                   related_name='superseded_by')
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    review_reason = models.TextField(blank=True)

    class Meta:
        db_table = 'application_facts'
        ordering = ['fact_type', 'created_at']
        indexes = [models.Index(fields=['application', 'fact_type', 'review_status'],
                                name='fact_application_type_idx')]

    def __str__(self):
        return f'{self.get_fact_type_display()} ({self.get_review_status_display()})'

    @property
    def is_current(self):
        return self.review_status != FactReviewStatus.SUPERSEDED


class CorrectionStatus(models.TextChoices):
    OPEN = 'open', 'Open'
    SUBMITTED = 'submitted', 'Corrections submitted'
    CANCELED = 'canceled', 'Canceled'


class CorrectionRequest(BaseModel):
    application = models.ForeignKey(Application, on_delete=models.PROTECT,
                                    related_name='correction_requests')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     related_name='+')
    requested_changes = models.JSONField()
    deadline_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=CorrectionStatus.choices,
                              default=CorrectionStatus.OPEN)
    submitted_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    resolved_at = models.DateTimeField(null=True, blank=True)
    exception_reason = models.TextField(blank=True)

    class Meta:
        db_table = 'correction_requests'
        ordering = ['-created_at']

    def window_open(self, now=None):
        return self.status == CorrectionStatus.OPEN and (now or timezone.now()) < self.deadline_at

    @property
    def message(self):
        return self.requested_changes.get('message', '')

    @property
    def document_type_codes(self):
        return self.requested_changes.get('document_types', [])


class WorkflowVisibility(models.TextChoices):
    APPLICANT = 'applicant', 'Visible to applicant'
    INTERNAL = 'internal', 'Internal'


class WorkflowLog(AppendOnlyModel):
    application = models.ForeignKey(Application, on_delete=models.PROTECT,
                                    related_name='workflow_logs')
    from_status = models.CharField(max_length=30, blank=True)
    to_status = models.CharField(max_length=30, choices=ApplicationStatus.choices)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              null=True, blank=True, related_name='+')
    reason = models.TextField(blank=True)
    visibility = models.CharField(max_length=20, choices=WorkflowVisibility.choices,
                                  default=WorkflowVisibility.APPLICANT)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'workflow_logs'
        ordering = ['occurred_at']


class ApplicationCorrection(AppendOnlyModel):
    """Field-level change history with reason (FR-004, FR-025)."""

    application = models.ForeignKey(Application, on_delete=models.PROTECT,
                                    related_name='corrections')
    target_entity = models.CharField(max_length=100)
    target_id = models.CharField(max_length=64)
    field_name = models.CharField(max_length=100)
    old_value = models.JSONField(null=True, blank=True)
    new_value = models.JSONField(null=True, blank=True)
    reason = models.TextField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              related_name='+')
    corrected_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'application_corrections'
        ordering = ['corrected_at']


# ==========================================================================
# Human recruitment decisions (Stage 7, Batch E)
# ==========================================================================

class DecisionType(models.TextChoices):
    SHORTLISTING = 'shortlisting', 'Shortlisting'
    COMMITTEE_RECOMMENDATION = 'committee_recommendation', 'Committee recommendation'
    FINAL_OUTCOME = 'final_outcome', 'Final outcome'
    APPOINTMENT = 'appointment', 'Appointment'


class DecisionOutcome(models.TextChoices):
    SHORTLISTED = 'shortlisted', 'Shortlisted'
    NOT_SHORTLISTED = 'not_shortlisted', 'Not shortlisted'
    RECOMMENDED = 'recommended', 'Recommended'
    NOT_RECOMMENDED = 'not_recommended', 'Not recommended'
    SELECTED = 'selected', 'Selected'
    NOT_SELECTED = 'not_selected', 'Not selected'
    CLOSED_WITHOUT_SELECTION = 'closed_without_selection', 'Closed without selection'
    APPOINTED = 'appointed', 'Appointed'


RELEASE_FIELDS = frozenset({'released_by', 'released_at', 'release_note', 'updated_at'})


class RecruitmentDecision(BaseModel):
    """A recorded human decision. Shortlisting, committee recommendation,
    final outcome, and appointment are separate events. A correction is a new
    record that supersedes the previous one; only the release fields of an
    existing record may change. Internal recording and applicant-facing
    release are separate actions."""

    application = models.ForeignKey(Application, on_delete=models.PROTECT,
                                    related_name='decisions')
    committee = models.ForeignKey('committee.Committee', on_delete=models.PROTECT,
                                  null=True, blank=True, related_name='decisions')
    decision_type = models.CharField(max_length=30, choices=DecisionType.choices)
    outcome = models.CharField(max_length=30, choices=DecisionOutcome.choices)
    reason = models.TextField()
    supporting_references = models.JSONField(default=dict, blank=True)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                   related_name='+')
    decision_at = models.DateTimeField()
    supersedes = models.OneToOneField('self', on_delete=models.PROTECT, null=True, blank=True,
                                      related_name='superseded_by')
    released_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')
    released_at = models.DateTimeField(null=True, blank=True)
    release_note = models.TextField(blank=True, help_text='Applicant-facing message.')
    appointment_reference = models.CharField(max_length=100, blank=True)
    appointment_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'recruitment_decisions'
        ordering = ['decision_at', 'created_at']
        indexes = [models.Index(fields=['application', 'decision_type'],
                                name='decision_application_type_idx')]
        constraints = [
            models.CheckConstraint(
                condition=~Q(decision_type='appointment')
                | (Q(appointment_at__isnull=False) & ~Q(appointment_reference='')),
                name='decision_appointment_has_reference'),
            models.CheckConstraint(
                condition=~Q(decision_type='committee_recommendation')
                | Q(committee__isnull=False),
                name='decision_recommendation_has_committee'),
            models.CheckConstraint(
                condition=(Q(released_at__isnull=True) & Q(released_by__isnull=True))
                | (Q(released_at__isnull=False) & Q(released_by__isnull=False)),
                name='decision_release_complete'),
        ]

    def __str__(self):
        return f'{self.get_decision_type_display()}: {self.get_outcome_display()}'

    def save(self, *args, **kwargs):
        if not self._state.adding:
            fields = kwargs.get('update_fields')
            if fields is None or not set(fields) <= RELEASE_FIELDS:
                raise PermissionError('Recorded decisions cannot be edited; record a '
                                      'correction instead.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError('Recorded decisions cannot be deleted.')

    @property
    def is_current(self):
        return not hasattr(self, 'superseded_by')

    @property
    def is_released(self):
        return self.released_at is not None
