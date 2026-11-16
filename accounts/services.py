"""Account business operations. Each mutating operation runs in a transaction
together with its audit event."""

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import signing
from django.core.exceptions import PermissionDenied
from django.core.mail import send_mail
from django.db import transaction
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone

from audit import services as audit
from audit.context import audit_reason

from . import roles
from .models import PrivacyAcknowledgment

VERIFY_SALT = 'accounts.email-verification'


class VerificationError(Exception):
    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind  # 'expired' or 'invalid'


# --------------------------------------------------------------------------
# Registration and verification (FR-003)
# --------------------------------------------------------------------------

@transaction.atomic
def register_applicant(*, username, email, password, first_name, last_name,
                       middle_name=None, notice_version=None):
    """Create an applicant account. Public registration can only ever create
    an Applicant: no staff flags or other roles are accepted here."""
    User = get_user_model()
    user = User.objects.create_user(
        username=username, email=email, password=password, first_name=first_name,
        last_name=last_name, middle_name=middle_name or None,
        is_staff=False, is_superuser=False,
    )
    audit.record('account.registered', entity=user, actor=user,
                 changes={'username': user.username, 'email': user.email})
    with audit_reason('Self-registration'):
        user.groups.add(Group.objects.get(name=roles.APPLICANT))
    acknowledge_privacy(user, notice_version or settings.PRIVACY_NOTICE_VERSION)
    return user


def make_verification_token(user):
    return signing.dumps({'u': str(user.pk), 'e': user.email}, salt=VERIFY_SALT)


def send_verification_email(request, user):
    token = make_verification_token(user)
    link = request.build_absolute_uri(reverse('accounts:verify_email', args=[token]))
    context = {'user': user, 'link': link,
               'hours': settings.EMAIL_VERIFICATION_TIMEOUT // 3600}
    send_mail(
        subject=render_to_string('accounts/emails/verify_email_subject.txt', context).strip(),
        message=render_to_string('accounts/emails/verify_email.txt', context),
        from_email=None,
        recipient_list=[user.email],
    )


@transaction.atomic
def verify_email(token):
    """Mark the email verified. Links expire, and stop working if the account
    email changes. Re-using a valid link is harmless."""
    try:
        data = signing.loads(token, salt=VERIFY_SALT,
                             max_age=settings.EMAIL_VERIFICATION_TIMEOUT)
    except signing.SignatureExpired:
        raise VerificationError('expired')
    except signing.BadSignature:
        raise VerificationError('invalid')
    User = get_user_model()
    user = User.objects.select_for_update().filter(pk=data.get('u')).first()
    if user is None or user.email != data.get('e') or not user.is_active:
        raise VerificationError('invalid')
    if user.email_verified_at is None:
        user.email_verified_at = timezone.now()
        user.save(update_fields=['email_verified_at'])
        audit.record('account.email_verified', entity=user, actor=user)
    return user


def requires_email_verification(user):
    """Self-registered applicants must verify their email before signing in.
    Staff accounts are created and vouched for by administrators."""
    return user.email_verified_at is None and roles.has_role(user, roles.APPLICANT) \
        and not roles.has_role(user, *roles.STAFF_ROLES)


def resend_verification(request, email):
    """Send a new link if an unverified account uses this email. Callers show
    the same response either way, so account existence is not revealed."""
    User = get_user_model()
    user = User.objects.filter(email__iexact=(email or '').strip(), is_active=True,
                               email_verified_at__isnull=True).first()
    if user is not None:
        send_verification_email(request, user)
        audit.record('account.verification_resent', entity=user, actor=None)


# --------------------------------------------------------------------------
# Privacy notice (FR-003)
# --------------------------------------------------------------------------

def needs_privacy_acknowledgment(user):
    if not roles.has_role(user, roles.APPLICANT):
        return False
    return not PrivacyAcknowledgment.objects.filter(
        user=user, notice_version=settings.PRIVACY_NOTICE_VERSION).exists()


@transaction.atomic
def acknowledge_privacy(user, notice_version):
    existing = PrivacyAcknowledgment.objects.filter(
        user=user, notice_version=notice_version).first()
    if existing:
        return existing
    ack = PrivacyAcknowledgment.objects.create(user=user, notice_version=notice_version)
    audit.record('privacy.acknowledged', entity=ack, actor=user,
                 changes={'notice_version': notice_version})
    return ack


# --------------------------------------------------------------------------
# Role assignment and account status (FR-002)
# --------------------------------------------------------------------------

def _require_admin(actor, target):
    if not roles.has_role(actor, roles.SYSTEM_ADMIN):
        raise PermissionDenied('Only System Administrators can change account access.')
    if actor.pk == target.pk:
        raise PermissionDenied('You cannot change your own roles or account status.')


@transaction.atomic
def set_roles(target, role_names, *, reason=''):
    """Make the target's roles exactly `role_names`. Each added/removed role is
    audited by the groups signal handler. No permission check: callers must
    authorize (see update_user_access; the CLI is trusted)."""
    unknown = set(role_names) - set(roles.ALL_ROLES)
    if unknown:
        raise ValueError(f'Unknown roles: {", ".join(sorted(unknown))}')
    wanted = set(Group.objects.filter(name__in=role_names))
    if len(wanted) != len(set(role_names)):
        raise ValueError('Roles are not set up yet. Run: manage.py seed_reference_data')
    current = set(target.groups.filter(name__in=roles.ALL_ROLES))
    with audit_reason(reason):
        if current - wanted:
            target.groups.remove(*(current - wanted))
        if wanted - current:
            target.groups.add(*(wanted - current))
    roles.clear_role_cache(target)


@transaction.atomic
def update_user_access(actor, target, *, role_names, is_active, reason):
    _require_admin(actor, target)
    set_roles(target, role_names, reason=reason)
    if target.is_active != is_active:
        target.is_active = is_active
        target.save(update_fields=['is_active'])
        audit.record('account.enabled' if is_active else 'account.disabled',
                     entity=target, reason=reason,
                     changes={'is_active': {'old': not is_active, 'new': is_active}})
