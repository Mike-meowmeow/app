"""Creating and delivering notifications (FR-014).

Reliability rule (transaction-bound outbox): `notify()` is called inside the
business transaction, so the notification row commits or rolls back with the
business change. Email is attempted only after the commit (on_commit), and
every attempt is recorded. If the process stops before sending, or sending
fails, `retry_pending()` (manage.py send_notifications, or the System
Administrator screen) sends it later. A notification is never created twice
for the same recipient and event (unique user + event_key), and a sent email
is never sent again.

Bodies are deliberately generic: no document contents, internal notes,
evaluation text, or correction details; the recipient opens the system to
see what they are permitted to see.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.db.models import Count, Q
from django.utils import timezone

from audit import services as audit

from .models import Channel, DeliveryStatus, Notification, NotificationDelivery

log = logging.getLogger(__name__)


def email_wanted(user):
    return bool(settings.NOTIFICATION_EMAIL_ENABLED and user.email and user.is_active)


def notify(user, *, category, event_key, title, message, application=None, interview=None,
           roster=None):
    """Create one notification for one recipient (idempotent per event)."""
    if user is None or not user.is_active:
        return None
    event_key = event_key[:200]
    existing = Notification.objects.filter(user=user, event_key=event_key).first()
    if existing:
        return existing
    try:
        with transaction.atomic():  # savepoint: a concurrent duplicate must not break the caller
            notification = Notification.objects.create(
                user=user, event_type=category, event_key=event_key, title=title[:200],
                message=message, application=application, interview=interview, roster=roster)
    except IntegrityError:
        return Notification.objects.get(user=user, event_key=event_key)
    if email_wanted(user):
        pk = notification.pk
        transaction.on_commit(lambda: deliver(pk))
    return notification


def notify_many(users, **kwargs):
    seen, result = set(), []
    for user in users:
        if user is not None and user.pk not in seen:
            seen.add(user.pk)
            result.append(notify(user, **kwargs))
    return [n for n in result if n]


def _email_body(notification):
    link = settings.SITE_URL.rstrip('/') + notification.target_url()
    return (f'{notification.message}\n\nOpen the recruitment system to see the details:\n'
            f'{link}\n\nThis is an automated message from EVSU-Ormoc HRMDO recruitment.')


def deliver(notification_id):
    """Attempt one email delivery. Never raises; the outcome is recorded."""
    try:
        with transaction.atomic():
            notification = Notification.objects.select_for_update().select_related('user') \
                .get(pk=notification_id)
            deliveries = notification.deliveries.filter(channel=Channel.EMAIL)
            if deliveries.filter(status=DeliveryStatus.SENT).exists():
                return None  # already delivered; a retry never sends twice
            if not email_wanted(notification.user):
                return None
            attempt = NotificationDelivery.objects.create(
                notification=notification, channel=Channel.EMAIL,
                attempt_number=deliveries.count() + 1)
        try:
            send_mail(f'[EVSU Recruitment] {notification.title}', _email_body(notification),
                      settings.DEFAULT_FROM_EMAIL, [notification.user.email],
                      fail_silently=False)
        except Exception as exc:  # SMTP errors vary by backend
            attempt.status = DeliveryStatus.FAILED
            attempt.error_message = f'{type(exc).__name__}: {exc}'[:2000]
            attempt.save(update_fields=['status', 'error_message'])
            log.warning('Notification email failed (%s): %s', notification_id, exc)
        else:
            attempt.status = DeliveryStatus.SENT
            attempt.delivered_at = timezone.now()
            attempt.save(update_fields=['status', 'delivered_at'])
        return attempt
    except Exception:  # a delivery problem must never break the business request
        log.exception('Notification delivery could not be recorded (%s)', notification_id)
        return None


def pending_email():
    """Notifications whose email copy is still owed: no sent attempt and fewer
    than the allowed number of attempts."""
    return Notification.objects.filter(user__is_active=True).exclude(user__email='') \
        .annotate(sent=Count('deliveries', filter=Q(deliveries__status=DeliveryStatus.SENT)),
                  attempts=Count('deliveries')) \
        .filter(sent=0, attempts__lt=settings.NOTIFICATION_MAX_ATTEMPTS)


def retry_pending(actor=None, *, notification_ids=None):
    """Retry owed email copies. Returns (sent, failed)."""
    if not settings.NOTIFICATION_EMAIL_ENABLED:
        return 0, 0
    qs = pending_email()
    if notification_ids is not None:
        qs = qs.filter(pk__in=notification_ids)
    sent = failed = 0
    for pk in list(qs.values_list('pk', flat=True)):
        attempt = deliver(pk)
        if attempt is None:
            continue
        if attempt.status == DeliveryStatus.SENT:
            sent += 1
        else:
            failed += 1
    if sent or failed:
        audit.record('notification.retry', actor=actor, entity_type='notifications.Notification',
                     changes={'sent': sent, 'failed': failed})
    return sent, failed


def unread_count(user):
    return Notification.objects.filter(user=user, read_at__isnull=True).count()


def mark_read(user, notification):
    if notification.user_id != user.pk:
        raise PermissionError('Not your notification.')
    if notification.read_at is None:
        notification.read_at = timezone.now()
        notification.save(update_fields=['read_at'])
    return notification


def mark_all_read(user):
    return Notification.objects.filter(user=user, read_at__isnull=True) \
        .update(read_at=timezone.now(), updated_at=timezone.now())
