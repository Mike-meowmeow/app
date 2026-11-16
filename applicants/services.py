"""Applicant profile operations (FR-004). Applicants edit only their own
profile; every change is audited with before/after values."""

from django.core.exceptions import PermissionDenied
from django.db import transaction

from accounts import roles
from audit import services as audit

from .models import (Applicant, EducationalRecord, Eligibility, ProfessionalLicense, Training,
                     WorkExperience)

USER_NAME_FIELDS = ('first_name', 'middle_name', 'last_name')
PERSONAL_FIELDS = ('contact_no', 'birthdate', 'sex', 'address', 'civil_status')

# kind -> (model, label). Used by URLs, forms, and templates.
RECORD_KINDS = {
    'education': (EducationalRecord, 'Education'),
    'experience': (WorkExperience, 'Work experience'),
    'training': (Training, 'Training'),
    'eligibility': (Eligibility, 'Eligibility'),
    'license': (ProfessionalLicense, 'Professional license'),
}


def _require_applicant(user):
    if not roles.has_role(user, roles.APPLICANT):
        raise PermissionDenied('Only applicants have a profile.')


@transaction.atomic
def get_profile(user):
    _require_applicant(user)
    profile, created = Applicant.objects.get_or_create(user=user)
    if created:
        audit.record('profile.created', entity=profile, actor=user)
    return profile


@transaction.atomic
def update_personal_information(user, data):
    profile = get_profile(user)
    before = {**audit.snapshot(user, USER_NAME_FIELDS), **audit.snapshot(profile, PERSONAL_FIELDS)}
    for field in USER_NAME_FIELDS:
        setattr(user, field, data.get(field) or (None if field == 'middle_name' else ''))
    for field in PERSONAL_FIELDS:
        setattr(profile, field, data.get(field) if data.get(field) is not None else '')
    if profile.birthdate == '':
        profile.birthdate = None
    user.full_clean(exclude=['password'])
    profile.full_clean()
    user.save(update_fields=list(USER_NAME_FIELDS))
    profile.save()
    profile.refresh_from_db()
    after = {**audit.snapshot(user, USER_NAME_FIELDS), **audit.snapshot(profile, PERSONAL_FIELDS)}
    changes = audit.diff(before, after)
    if changes:
        audit.record('profile.updated', entity=profile, actor=user, changes=changes)
    return profile


def get_record(user, kind, pk):
    """The applicant's own record, or DoesNotExist (other people's are invisible)."""
    model, _ = RECORD_KINDS[kind]
    return model.objects.get(pk=pk, applicant__user=user)


@transaction.atomic
def save_record(user, kind, instance):
    """Create or update a qualification record from a validated, unsaved form
    instance. Ownership is always forced to the acting applicant."""
    profile = get_profile(user)
    model, _ = RECORD_KINDS[kind]
    if instance.pk and not instance._state.adding:
        current = model.objects.select_for_update().get(pk=instance.pk, applicant=profile)
        before = audit.snapshot(current)
    else:
        before = None
    instance.applicant = profile
    instance.full_clean()
    instance.save()
    instance.refresh_from_db()  # compare stored values (e.g. 16 -> '16.00')
    after = audit.snapshot(instance)
    changes = audit.diff(before, after)
    if before is None or changes:
        audit.record(f'profile.{kind}_{"added" if before is None else "updated"}',
                     entity=instance, actor=user, changes=changes)
    return instance


@transaction.atomic
def delete_record(user, kind, pk):
    record = get_record(user, kind, pk)
    audit.record(f'profile.{kind}_removed', entity=record, actor=user,
                 changes=audit.snapshot(record))
    record.delete()
