"""Applicant profile operations (FR-004). Applicants edit only their own
profile; every change is audited with before/after values."""

from django.core.exceptions import PermissionDenied
from django.db import transaction

from accounts import roles
from audit import services as audit

from .models import (MAX_FILES_PER_RECORD, SECTION_DOCUMENT_TYPES, Applicant,
                     EducationalRecord, Eligibility, ProfessionalLicense, ProfileDocument,
                     Training, WorkExperience)

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
    for document in ProfileDocument.objects.filter(applicant=record.applicant, record_kind=kind,
                                                   record_id=record.pk):
        _delete_profile_document(user, document)
    record.delete()


# --------------------------------------------------------------------------
# Files attached to profile records
# --------------------------------------------------------------------------

def allowed_document_types(kind):
    """Active document types a profile section accepts, in the fixed order."""
    from documents.models import DocumentType
    codes = SECTION_DOCUMENT_TYPES.get(kind, ())
    types = {t.code: t for t in DocumentType.objects.filter(code__in=codes, is_active=True)}
    return [types[c] for c in codes if c in types]


def documents_by_record(profile):
    """{record_id: [ProfileDocument]} for the profile page."""
    found = {}
    for document in profile.profile_documents.select_related('document_type'):
        found.setdefault(document.record_id, []).append(document)
    return found


def attach_document(user, kind, record_pk, document_type, upload):
    """Store a file for one of the applicant's own records. The same type, size,
    and malware checks as application uploads apply."""
    from django.conf import settings
    from django.core.exceptions import ValidationError

    from documents import storage
    from documents.models import MalwareStatus

    record = get_record(user, kind, record_pk)  # ownership
    if document_type not in allowed_document_types(kind):
        raise ValidationError(f'{document_type.name} cannot be attached to '
                              f'{RECORD_KINDS[kind][1].lower()}.')
    profile = record.applicant
    if ProfileDocument.objects.filter(applicant=profile, record_kind=kind,
                                      record_id=record.pk).count() >= MAX_FILES_PER_RECORD:
        raise ValidationError(f'At most {MAX_FILES_PER_RECORD} files per entry. Remove one '
                              'first.')
    info = storage.inspect(upload, document_type)  # raises FileRejected; nothing stored
    key = storage.store(upload, f'profiles/{profile.pk}', info['extension'])
    try:
        status = storage.scan(key)
        if status == MalwareStatus.QUARANTINED:
            raise storage.FileRejected('The file was flagged by the malware scanner and was '
                                       'not saved.')
        if status == MalwareStatus.NOT_SCANNED and settings.MALWARE_SCAN_REQUIRED:
            raise ValidationError('The file could not be checked for malware right now. '
                                  'Please try again later.')
        with transaction.atomic():
            document = ProfileDocument.objects.create(
                applicant=profile, record_kind=kind, record_id=record.pk,
                document_type=document_type, file_name=info['file_name'], storage_key=key,
                file_size_bytes=info['size'], mime_type=info['mime_type'],
                sha256=info['sha256'], malware_status=status)
            audit.record('profile.document_attached', entity=document, actor=user,
                         changes={'record': f'{kind}:{record.pk}',
                                  'document_type': document_type.code,
                                  'file_name': info['file_name'], 'size': info['size'],
                                  'sha256': info['sha256'], 'malware_status': status})
    except Exception:
        storage.delete(key)
        raise
    return document


def _delete_profile_document(user, document):
    from documents import storage
    audit.record('profile.document_removed', entity=document, actor=user,
                 changes={'record': f'{document.record_kind}:{document.record_id}',
                          'file_name': document.file_name})
    key = document.storage_key
    document.delete()
    transaction.on_commit(lambda: storage.delete(key))


def get_profile_document(user, pk):
    """The applicant's own attached file, or DoesNotExist."""
    return ProfileDocument.objects.select_related('document_type').get(
        pk=pk, applicant__user=user)


@transaction.atomic
def remove_document(user, pk):
    """Remove an attached file. Copies already in applications are kept."""
    _delete_profile_document(user, get_profile_document(user, pk))
