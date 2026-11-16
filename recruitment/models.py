from django.conf import settings
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from core.models import BaseModel


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
