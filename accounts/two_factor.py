"""Two-factor sign-in for staff accounts (FR-001).

Staff confirm each sign-in with a 6-digit code from an authenticator app
(time-based one-time passwords, RFC 6238: 30-second steps, SHA-1, the format
every common authenticator app reads from the QR code), or with a single-use
recovery code. Applicant accounts never use it.

When it applies: to every staff account when STAFF_TWO_FACTOR_REQUIRED is on
(the production default), and to any staff account that has set it up even when
it is off. A staff member without an authenticator sets one up right after their
first password sign-in; after that only a System Administrator can reset it.

Implemented with the standard library (hmac, hashlib) so no extra package is
needed; the QR image is drawn with ReportLab's encoder and Pillow, which the
roster exports already use.
"""

import base64
import hashlib
import hmac
import io
import secrets
import struct
import time
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from audit import services as audit

from . import roles
from .models import StaffRecoveryCode, StaffTwoFactor

STEP_SECONDS = 30
DIGITS = 6
# Accept the previous and next step too, for phone clocks a little off.
ALLOWED_DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10
RECOVERY_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'   # no 0/O or 1/I


# --------------------------------------------------------------------------
# Time-based one-time passwords
# --------------------------------------------------------------------------

def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode('ascii')


def _key(secret):
    return base64.b32decode(secret + '=' * (-len(secret) % 8), casefold=True)


def code_at(secret, step):
    digest = hmac.new(_key(secret), struct.pack('>Q', step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10 ** DIGITS).zfill(DIGITS)


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP_SECONDS)


def _matching_step(secret, code, now=None):
    step = current_step(now)
    for candidate in (step, step - ALLOWED_DRIFT_STEPS, step + ALLOWED_DRIFT_STEPS):
        if hmac.compare_digest(code_at(secret, candidate), code):
            return candidate
    return None


def normalize(code):
    return ''.join(ch for ch in (code or '') if ch.isalnum()).upper()


def provisioning_uri(user, secret):
    issuer = settings.TWO_FACTOR_ISSUER
    label = quote(f'{issuer}:{user.username}')
    return (f'otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}'
            f'&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}')


def qr_png_data_uri(text, scale=6, border=4):
    from PIL import Image
    from reportlab.graphics.barcode import qrencoder

    qr = qrencoder.QRCode(None, qrencoder.QRErrorCorrectLevel.M)
    qr.addData(text)
    qr.make()
    count = qr.getModuleCount()
    size = count + 2 * border
    image = Image.new('1', (size, size), 1)
    pixels = image.load()
    for row in range(count):
        for col in range(count):
            if qr.isDark(row, col):
                pixels[col + border, row + border] = 0
    image = image.resize((size * scale, size * scale), Image.NEAREST)
    buffer = io.BytesIO()
    image.save(buffer, 'PNG')
    return 'data:image/png;base64,' + base64.b64encode(buffer.getvalue()).decode('ascii')


def grouped(secret):
    return ' '.join(secret[i:i + 4] for i in range(0, len(secret), 4))


# --------------------------------------------------------------------------
# Who it applies to
# --------------------------------------------------------------------------

def is_enrolled(user):
    return StaffTwoFactor.objects.filter(user=user, confirmed_at__isnull=False).exists()


def applies_to(user):
    if not roles.is_staff_account(user):
        return False
    return settings.STAFF_TWO_FACTOR_REQUIRED or is_enrolled(user)


def status(user):
    device = StaffTwoFactor.objects.filter(user=user, confirmed_at__isnull=False).first()
    return {
        'enrolled': device is not None,
        'since': device.confirmed_at if device else None,
        'recovery_codes_left': recovery_codes_left(user) if device else 0,
        'required': settings.STAFF_TWO_FACTOR_REQUIRED,
    }


# --------------------------------------------------------------------------
# Setting up, checking, recovery codes, reset
# --------------------------------------------------------------------------

def start_setup(user):
    """The not-yet-confirmed authenticator for this user. The secret stays the
    same across page reloads, so a QR code already scanned keeps working."""
    if not roles.is_staff_account(user):
        raise PermissionDenied('Two-factor sign-in is for staff accounts.')
    device, _ = StaffTwoFactor.objects.get_or_create(user=user,
                                                      defaults={'secret': new_secret()})
    if device.confirmed_at:
        raise PermissionDenied('Two-factor sign-in is already set up for this account.')
    return device


@transaction.atomic
def confirm_setup(user, code):
    """Turn the authenticator on when the code matches. Returns the new
    recovery codes (shown once), or None for a wrong code."""
    device = StaffTwoFactor.objects.select_for_update().get(user=user)
    if device.confirmed_at:
        raise PermissionDenied('Two-factor sign-in is already set up for this account.')
    step = _matching_step(device.secret, normalize(code))
    if step is None:
        return None
    device.confirmed_at = timezone.now()
    device.last_used_step = step
    device.save(update_fields=['confirmed_at', 'last_used_step'])
    codes = _replace_recovery_codes(user)
    audit.record('auth.two_factor_enabled', entity=user, actor=user)
    return codes


@transaction.atomic
def verify(user, code):
    """Check a sign-in code. Returns 'app' or 'recovery' on success, else None.
    An app code is accepted only once, so a code seen over someone's shoulder
    cannot be replayed within its 30 seconds."""
    code = normalize(code)
    device = StaffTwoFactor.objects.select_for_update() \
        .filter(user=user, confirmed_at__isnull=False).first()
    if device is None or not code:
        return None
    if code.isdigit() and len(code) == DIGITS:
        step = _matching_step(device.secret, code)
        if step is None or (device.last_used_step is not None
                            and step <= device.last_used_step):
            return None
        device.last_used_step = step
        device.save(update_fields=['last_used_step'])
        return 'app'
    recovery = StaffRecoveryCode.objects.select_for_update() \
        .filter(user=user, code_hash=_hash(code), used_at__isnull=True).first()
    if recovery is None:
        return None
    recovery.used_at = timezone.now()
    recovery.save(update_fields=['used_at'])
    audit.record('auth.recovery_code_used', entity=user, actor=user,
                 changes={'remaining': recovery_codes_left(user)})
    return 'recovery'


def recovery_codes_left(user):
    return StaffRecoveryCode.objects.filter(user=user, used_at__isnull=True).count()


@transaction.atomic
def regenerate_recovery_codes(user):
    if not is_enrolled(user):
        raise PermissionDenied('Set up two-factor sign-in first.')
    codes = _replace_recovery_codes(user)
    audit.record('auth.recovery_codes_regenerated', entity=user, actor=user)
    return codes


@transaction.atomic
def reset(actor, user, reason):
    """System Administrator: remove a staff member's authenticator (lost or
    replaced phone). They set up a new one at their next sign-in."""
    if not roles.has_role(actor, roles.SYSTEM_ADMIN):
        raise PermissionDenied('Only a System Administrator can reset two-factor sign-in.')
    if actor.pk == user.pk:
        raise PermissionDenied('Ask another System Administrator to reset your own '
                               'two-factor sign-in.')
    removed = StaffTwoFactor.objects.filter(user=user).delete()[0]
    StaffRecoveryCode.objects.filter(user=user).delete()
    audit.record('auth.two_factor_reset', entity=user, actor=actor, reason=reason,
                 changes={'had_authenticator': bool(removed)})
    return bool(removed)


def _hash(code):
    return hashlib.sha256(normalize(code).encode('ascii')).hexdigest()


def _new_recovery_code():
    raw = ''.join(secrets.choice(RECOVERY_ALPHABET) for _ in range(10))
    return f'{raw[:5]}-{raw[5:]}'


def _replace_recovery_codes(user):
    StaffRecoveryCode.objects.filter(user=user).delete()
    codes = [_new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    StaffRecoveryCode.objects.bulk_create(
        StaffRecoveryCode(user=user, code_hash=_hash(code), created_at=timezone.now(),
                          updated_at=timezone.now()) for code in codes)
    return codes
