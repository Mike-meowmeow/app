"""Document uploads, replacement, and human verification (FR-007, FR-008).

Storage and database are kept consistent: a file is written first under a
random private name, then the rows are created in one transaction; if the
transaction fails, the file is deleted, so no dangling file or reference
remains. Removing a draft document deletes its files only after commit.
"""

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from audit import services as audit
from recruitment import application_selectors as access
from recruitment import application_services as applications
from recruitment.models import Application, ApplicationStatus

from . import storage
from .models import (Document, DocumentType, DocumentVerification, DocumentVersion,
                     MalwareStatus, ValidationStatus, VerificationStatus)


def _upload_context(user, application):
    """Which correction request (if any) an upload belongs to; raises if the
    applicant may not change documents right now."""
    if not access.is_owner(user, application):
        raise PermissionDenied('This is not your application.')
    if application.status == ApplicationStatus.DRAFT:
        return None
    request = application.open_correction_request()
    if application.status == ApplicationStatus.CORRECTION_REQUESTED and request:
        if not request.window_open():
            raise ValidationError('The correction window has closed. Contact HR for an '
                                  'exception.')
        return request
    raise ValidationError('Documents cannot be changed after submission unless HR requests '
                          'a correction.')


def _requirement_for(application, document_type):
    req = applications.requirements_for(application)
    item = req.document_requirements.filter(document_type=document_type).first() if req else None
    if item is None:
        raise ValidationError(f'{document_type.name} is not part of this vacancy\'s checklist.')
    return item


def upload(user, application, document_type, upload_file, *, replaces=None):
    """Add a document (or a new version of `replaces`). Returns the version."""
    application = Application.objects.get(pk=application.pk)
    correction = _upload_context(user, application)
    if replaces is not None:
        if replaces.application_id != application.pk:
            raise PermissionDenied('That document belongs to another application.')
        document_type = replaces.document_type
    requirement = _requirement_for(application, document_type)
    if replaces is None and application.documents.filter(
            document_type=document_type).count() >= requirement.max_files:
        raise ValidationError(f'{document_type.name}: at most {requirement.max_files} file(s). '
                              'Replace an existing file instead.')

    info = storage.inspect(upload_file, document_type)  # raises FileRejected; nothing stored
    key = storage.store(upload_file, application.pk, info['extension'])
    try:
        with transaction.atomic():
            Application.objects.select_for_update().get(pk=application.pk)
            document = replaces or Document.objects.create(
                application=application, document_type=document_type, uploaded_by=user)
            number = (document.versions.order_by('-version').values_list('version', flat=True)
                      .first() or 0) + 1
            version = DocumentVersion.objects.create(
                document=document, version=number, correction_request=correction,
                file_name=info['file_name'], storage_key=key, file_size_bytes=info['size'],
                mime_type=info['mime_type'], sha256=info['sha256'],
                validation_status=ValidationStatus.VALID, uploaded_by=user)
            status = storage.scan(key)
            if status == MalwareStatus.NOT_SCANNED and settings.MALWARE_SCAN_REQUIRED:
                raise ValidationError('The file could not be checked for malware right now. '
                                      'Please try again later.')
            if status != MalwareStatus.NOT_SCANNED:
                version.malware_status = status
                version.save(update_fields=['malware_status'])
            if correction is not None:
                applications._bump_revision(application)
            audit.record('document.replaced' if replaces else 'document.uploaded',
                         entity=version, actor=user,
                         changes={'application': application.reference,
                                  'document_type': document_type.code, 'version': number,
                                  'file_name': info['file_name'], 'size': info['size'],
                                  'sha256': info['sha256'], 'malware_status': status,
                                  'correction_request': str(correction.pk) if correction else None})
    except Exception:
        storage.delete(key)
        raise
    return version


@transaction.atomic
def remove_draft_document(user, document):
    application = Application.objects.select_for_update().get(pk=document.application_id)
    if not access.is_owner(user, application):
        raise PermissionDenied('This is not your application.')
    if application.status != ApplicationStatus.DRAFT:
        raise ValidationError('Submitted documents are kept; upload a replacement instead.')
    keys = list(document.versions.values_list('storage_key', flat=True))
    audit.record('document.removed', entity=document, actor=user,
                 changes={'application': application.reference,
                          'document_type': document.document_type.code, 'versions': len(keys)})
    document.versions.all().delete()
    document.delete()
    transaction.on_commit(lambda: [storage.delete(k) for k in keys])


@transaction.atomic
def verify(actor, version, *, status, notes=''):
    """Record a human verification outcome for one exact file version. History
    is kept; the version's verification_status is updated to match."""
    version = DocumentVersion.objects.select_for_update().select_related(
        'document__application__vacancy').get(pk=version.pk)
    application = version.document.application
    if not access.can_review(actor, application):
        raise PermissionDenied('You are not assigned to review this vacancy.')
    if status not in (VerificationStatus.VERIFIED, VerificationStatus.INCOMPLETE,
                      VerificationStatus.INVALID, VerificationStatus.REJECTED):
        raise ValidationError('Choose a verification outcome.')
    if status != VerificationStatus.VERIFIED and not notes.strip():
        raise ValidationError('A reason is required for Incomplete, Invalid, or Rejected.')
    if version.document.current_version().pk != version.pk:
        raise ValidationError('Only the latest version of a document can be verified.')
    if not version.is_usable:
        raise ValidationError('This file failed technical checks and cannot be verified.')
    old = version.verification_status
    DocumentVerification.objects.create(document_version=version, status=status,
                                        reviewed_by=actor, notes=notes)
    version.verification_status = status
    version.save(update_fields=['verification_status'])
    applications._bump_revision(application)
    audit.record('document.verified' if status == VerificationStatus.VERIFIED
                 else 'document.verification_failed', entity=version, actor=actor, reason=notes,
                 changes={'verification_status': {'old': old, 'new': status},
                          'application': application.reference})
    return version


def can_download(user, version):
    return access.can_view(user, version.document.application)


def record_download(user, version):
    audit.record('document.downloaded', entity=version, actor=user,
                 changes={'application': version.document.application.reference,
                          'file_name': version.file_name})


def pending_verifications(user):
    """Latest versions awaiting verification on applications the user reviews."""
    versions = DocumentVersion.objects.select_related(
        'document__document_type', 'document__application__vacancy',
        'document__application__applicant__user'
    ).filter(
        verification_status=VerificationStatus.PENDING,
        validation_status=ValidationStatus.VALID,
        document__application__in=access.visible_applications(user).values('pk'),
    ).order_by('uploaded_at')
    return [v for v in versions if v.document.current_version().pk == v.pk
            and access.can_review(user, v.document.application)]
