import uuid

from django.db import models
from django.utils import timezone


class UUIDModel(models.Model):
    """Application-owned records use UUID primary keys."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    """created_at / updated_at, set from one clock reading on insert.

    Using a single timestamp keeps updated_at equal to created_at for
    append-only records, as Database Structure.md requires.
    """

    created_at = models.DateTimeField(editable=False)
    updated_at = models.DateTimeField(editable=False)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        now = timezone.now()
        if self._state.adding and self.created_at is None:
            self.created_at = now
            self.updated_at = now
        else:
            self.updated_at = now
            update_fields = kwargs.get('update_fields')
            if update_fields is not None:
                kwargs['update_fields'] = {*update_fields, 'updated_at'}
        super().save(*args, **kwargs)


class BaseModel(UUIDModel, TimeStampedModel):
    class Meta:
        abstract = True


class AppendOnlyModel(BaseModel):
    """Event/history records: inserted once, never updated or deleted
    through the ORM instance API. (QuerySet.update/delete are not intercepted;
    services must not use them on these models.)"""

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise PermissionError(f'{type(self).__name__} records are append-only.')
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError(f'{type(self).__name__} records cannot be deleted.')


class Department(BaseModel):
    name = models.CharField(max_length=150)
    code = models.CharField(max_length=32, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'departments'
        ordering = ['name']

    def __str__(self):
        return f'{self.code} - {self.name}'


class RecruitmentType(BaseModel):
    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=150)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'recruitment_types'
        ordering = ['name']

    def __str__(self):
        return self.name


class CredentialKind(models.TextChoices):
    LICENSE = 'license', 'Professional license (PRC)'
    ELIGIBILITY = 'eligibility', 'Civil service eligibility (CSC)'


class Credential(BaseModel):
    """A name in the reference list of licenses and eligibilities. HR's
    qualification standards and applicants' profiles suggest these names, and
    the advisory qualification checks treat a name and its other spellings
    (aliases, one per line) as the same credential. A name not on the list can
    still be typed. Kept up to date by System Administrators."""

    kind = models.CharField(max_length=20, choices=CredentialKind.choices)
    name = models.CharField(max_length=200)
    aliases = models.TextField(blank=True, help_text='Other spellings or abbreviations, one per line.')
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'credential_names'
        ordering = ['kind', 'name']
        constraints = [models.UniqueConstraint(
            fields=['kind', 'name'], name='credential_name_unique_per_kind',
            violation_error_message='This name is already in the list.')]

    def __str__(self):
        return self.name

    def alias_list(self):
        return [a.strip() for a in self.aliases.splitlines() if a.strip()]
