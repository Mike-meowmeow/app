"""Current, editable applicant profiles (FR-004). Submitted applications keep
their own snapshots (Stage 5), so later edits never alter past submissions."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from core.models import BaseModel

# Text, not a number: keeps leading zeros and country prefixes.
contact_number_validator = RegexValidator(
    r'^\+?[0-9][0-9 ()-]{6,19}$',
    'Enter a phone number such as 09171234567 or +63 917 123 4567.')


def _not_future(errors, field, value):
    if value and value > timezone.localdate():
        errors[field] = 'This date cannot be in the future.'


def _ordered(errors, start_field, start, end_field, end):
    if start and end and end < start:
        errors[end_field] = 'This date cannot be earlier than the start date.'


class Sex(models.TextChoices):
    MALE = 'male', 'Male'
    FEMALE = 'female', 'Female'


class CivilStatus(models.TextChoices):
    SINGLE = 'single', 'Single'
    MARRIED = 'married', 'Married'
    WIDOWED = 'widowed', 'Widowed'
    SEPARATED = 'separated', 'Separated'
    OTHER = 'other', 'Other'


class DegreeLevel(models.TextChoices):
    # Matches the official roster's degree columns. Other reference values
    # need HR confirmation (FR-026).
    BACHELOR = 'bachelor', "Bachelor's degree"
    MASTER = 'master', "Master's degree"
    DOCTORATE = 'doctorate', 'Doctorate'
    OTHER = 'other', 'Other (certificate, diploma, units)'


DEGREE_RANK = {DegreeLevel.BACHELOR: 1, DegreeLevel.MASTER: 2, DegreeLevel.DOCTORATE: 3,
               DegreeLevel.OTHER: 0}


class CompletionStatus(models.TextChoices):
    COMPLETED = 'completed', 'Completed'
    ONGOING = 'ongoing', 'Ongoing'
    NOT_COMPLETED = 'not_completed', 'Not completed'


class Applicant(BaseModel):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                related_name='applicant_profile')
    contact_no = models.CharField('contact number', max_length=30, blank=True,
                                  validators=[contact_number_validator])
    birthdate = models.DateField(null=True, blank=True)
    sex = models.CharField(max_length=10, choices=Sex.choices, blank=True)
    address = models.TextField(blank=True)
    civil_status = models.CharField(max_length=20, choices=CivilStatus.choices, blank=True)

    class Meta:
        db_table = 'applicants'


    def clean(self):
        errors = {}
        _not_future(errors, 'birthdate', self.birthdate)
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.user.full_name or self.user.username

    REQUIRED_PERSONAL = ('contact_no', 'birthdate', 'sex', 'address', 'civil_status')

    def missing_information(self):
        """Labels of personal fields still empty (checked again at submission)."""
        missing = [label for label, value in (('First name', self.user.first_name),
                                              ('Last name', self.user.last_name)) if not value]
        missing += [self._meta.get_field(f).verbose_name.capitalize()
                    for f in self.REQUIRED_PERSONAL if not getattr(self, f)]
        if not self.educational_records.exists():
            missing.append('At least one education record')
        return missing


class ProfileRecord(BaseModel):
    """Repeatable qualification record owned by one applicant."""

    class Meta:
        abstract = True


class EducationalRecord(ProfileRecord):
    applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE,
                                  related_name='educational_records')
    degree_level = models.CharField(max_length=20, choices=DegreeLevel.choices)
    program = models.CharField('degree / program', max_length=200)
    institution = models.CharField(max_length=200)
    completion_status = models.CharField(max_length=20, choices=CompletionStatus.choices)
    completed_on = models.DateField('date completed', null=True, blank=True)

    class Meta:
        db_table = 'educational_records'
        ordering = ['created_at']
        constraints = [
            models.CheckConstraint(
                condition=Q(completed_on__isnull=True) | Q(completion_status='completed'),
                name='education_date_only_when_completed'),
        ]


    def clean(self):
        errors = {}
        if self.completed_on and self.completion_status != CompletionStatus.COMPLETED:
            errors['completed_on'] = 'Only completed education has a completion date.'
        _not_future(errors, 'completed_on', self.completed_on)
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.get_degree_level_display()}: {self.program}'


class WorkExperience(ProfileRecord):
    applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE,
                                  related_name='work_experiences')
    position = models.CharField(max_length=200)
    organization = models.CharField('employer / organization', max_length=200)
    duties = models.TextField(blank=True)
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True,
                                help_text='Leave blank if this is your current position.')

    class Meta:
        db_table = 'work_experiences'
        ordering = ['-start_date']
        constraints = [
            models.CheckConstraint(condition=Q(end_date__isnull=True)
                                   | Q(end_date__gte=F('start_date')),
                                   name='work_experience_dates_ordered'),
        ]


    def clean(self):
        errors = {}
        _not_future(errors, 'start_date', self.start_date)
        _ordered(errors, 'start_date', self.start_date, 'end_date', self.end_date)
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.position}, {self.organization}'


class Training(ProfileRecord):
    applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE,
                                  related_name='trainings')
    title = models.CharField(max_length=255)
    hours = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True,
                                validators=[MinValueValidator(0)])
    provider = models.CharField('conducted by', max_length=200, blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)

    class Meta:
        db_table = 'trainings'
        ordering = ['-start_date', 'created_at']
        constraints = [
            models.CheckConstraint(condition=Q(hours__isnull=True) | Q(hours__gte=0),
                                   name='training_hours_nonnegative'),
            models.CheckConstraint(condition=Q(start_date__isnull=True)
                                   | Q(end_date__isnull=True)
                                   | Q(end_date__gte=F('start_date')),
                                   name='training_dates_ordered'),
        ]


    def clean(self):
        errors = {}
        _not_future(errors, 'start_date', self.start_date)
        _ordered(errors, 'start_date', self.start_date, 'end_date', self.end_date)
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.title


class Eligibility(ProfileRecord):
    applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE,
                                  related_name='eligibilities')
    eligibility_type = models.CharField('eligibility', max_length=200)
    rating = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                 validators=[MinValueValidator(0), MaxValueValidator(100)])
    date_obtained = models.DateField(null=True, blank=True)

    class Meta:
        db_table = 'eligibilities'
        ordering = ['created_at']
        verbose_name_plural = 'eligibilities'
        constraints = [
            models.CheckConstraint(condition=Q(rating__isnull=True)
                                   | (Q(rating__gte=0) & Q(rating__lte=100)),
                                   name='eligibility_rating_range'),
        ]


    def clean(self):
        errors = {}
        _not_future(errors, 'date_obtained', self.date_obtained)
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return self.eligibility_type


class ProfessionalLicense(ProfileRecord):
    applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE,
                                  related_name='professional_licenses')
    profession = models.CharField(max_length=200)
    license_number = models.CharField(max_length=50)
    issuing_body = models.CharField(max_length=200, blank=True, help_text='e.g. PRC')
    issued_on = models.DateField(null=True, blank=True)
    expires_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = 'professional_licenses'
        ordering = ['created_at']
        constraints = [
            models.CheckConstraint(condition=Q(issued_on__isnull=True)
                                   | Q(expires_on__isnull=True)
                                   | Q(expires_on__gte=F('issued_on')),
                                   name='license_dates_ordered'),
        ]


    def clean(self):
        errors = {}
        _not_future(errors, 'issued_on', self.issued_on)
        _ordered(errors, 'issued_on', self.issued_on, 'expires_on', self.expires_on)
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f'{self.profession} ({self.license_number})'


# Document types each profile section accepts (codes from seed_reference_data).
SECTION_DOCUMENT_TYPES = {
    'education': ('TOR', 'DIPLOMA'),
    'experience': ('EMPLOYMENT',),
    'training': ('TRAINING',),
    'eligibility': ('PRC',),
    'license': ('PRC',),
}
MAX_FILES_PER_RECORD = 3


class ProfileDocument(BaseModel):
    """A supporting file attached to one profile record (e.g. a transcript on
    an education entry). It stays with the profile for reuse; applications get
    their own copy (documents.Document.profile_document), so removing or
    changing it never alters a submitted application."""

    applicant = models.ForeignKey(Applicant, on_delete=models.CASCADE,
                                  related_name='profile_documents')
    record_kind = models.CharField(max_length=20)
    record_id = models.UUIDField()
    document_type = models.ForeignKey('documents.DocumentType', on_delete=models.PROTECT,
                                      related_name='profile_documents')
    file_name = models.CharField(max_length=255)
    storage_key = models.CharField(max_length=255, unique=True)
    file_size_bytes = models.PositiveBigIntegerField()
    mime_type = models.CharField(max_length=100)
    sha256 = models.CharField(max_length=64)
    malware_status = models.CharField(max_length=20, default='not_scanned')
    uploaded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'profile_documents'
        ordering = ['uploaded_at']
        indexes = [models.Index(fields=['applicant', 'record_kind', 'record_id'],
                                name='profile_document_record_idx')]

    def __str__(self):
        return f'{self.file_name} ({self.record_kind})'
