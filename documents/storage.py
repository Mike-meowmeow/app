"""Private document storage and technical file validation (FR-007).

Files live under settings.DOCUMENT_STORAGE_ROOT, outside any static or media
URL, and are only served through permission-checked views. File type is
decided from the file's content (magic bytes), not from its name or the
browser-supplied content type.
"""

import hashlib
import shutil
import subprocess
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import ValidationError

from .models import MalwareStatus

SIGNATURES = {
    'application/pdf': (b'%PDF-',),
    'image/jpeg': (b'\xff\xd8\xff',),
    'image/png': (b'\x89PNG\r\n\x1a\n',),
}
EXTENSIONS = {
    'application/pdf': ('.pdf',),
    'image/jpeg': ('.jpg', '.jpeg'),
    'image/png': ('.png',),
}
LABELS = {'application/pdf': 'PDF', 'image/jpeg': 'JPG', 'image/png': 'PNG'}


class FileRejected(ValidationError):
    pass


def root():
    return Path(settings.DOCUMENT_STORAGE_ROOT)


def inspect(upload, document_type):
    """Validate an uploaded file against its document type. Returns metadata or
    raises FileRejected with an applicant-friendly message. Nothing is stored."""
    allowed = [m for m in document_type.allowed_mime_types if m in SIGNATURES]
    allowed_label = ', '.join(LABELS[m] for m in allowed) or 'none'
    size = upload.size or 0
    if size == 0:
        raise FileRejected('The file is empty or could not be read.')
    if size > document_type.max_size_bytes:
        raise FileRejected(f'The file is larger than the {document_type.max_size_mb} MB limit '
                           f'for {document_type.name}.')

    upload.seek(0)
    head = upload.read(16)
    mime = next((m for m in allowed if any(head.startswith(sig) for sig in SIGNATURES[m])), None)
    if mime is None:
        raise FileRejected(f'This file type is not accepted for {document_type.name}. '
                           f'Allowed: {allowed_label}.')
    extension = Path(upload.name or '').suffix.lower()
    if extension not in EXTENSIONS[mime]:
        raise FileRejected(f'The file name extension does not match its content '
                           f'({LABELS[mime]}). Rename it to end in {EXTENSIONS[mime][0]}.')

    digest = hashlib.sha256()
    upload.seek(0)
    for chunk in upload.chunks():
        digest.update(chunk)
    upload.seek(0)
    return {'mime_type': mime, 'size': size, 'sha256': digest.hexdigest(),
            'extension': extension, 'file_name': Path(upload.name).name[:255]}


def store(upload, application_id, extension):
    """Write the file under a random name. Returns the storage key."""
    key = f'{application_id}/{uuid.uuid4().hex}{extension}'
    path = root() / key
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as out:
        for chunk in upload.chunks():
            out.write(chunk)
    upload.seek(0)
    return key


def path_for(key):
    """Absolute path for a storage key, refusing anything outside the root."""
    base = root().resolve()
    path = (base / key).resolve()
    if base not in path.parents:
        raise ValidationError('Invalid storage key.')
    return path


def delete(key):
    try:
        path_for(key).unlink(missing_ok=True)
    except (OSError, ValidationError):
        pass


def scan(key):
    """Run the configured malware scanner (e.g. "clamscan --no-summary").
    Exit 0 = clean, 1 = infected (file moved to quarantine). Without a scanner
    the file is honestly recorded as not scanned."""
    command = getattr(settings, 'MALWARE_SCAN_COMMAND', '')
    if not command:
        return MalwareStatus.NOT_SCANNED
    path = path_for(key)
    try:
        result = subprocess.run([*command.split(), str(path)], capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return MalwareStatus.NOT_SCANNED
    if result.returncode == 0:
        return MalwareStatus.CLEAN
    if result.returncode == 1:
        quarantine = root().parent / 'quarantine'
        quarantine.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(quarantine / path.name))
        return MalwareStatus.QUARANTINED
    return MalwareStatus.NOT_SCANNED
