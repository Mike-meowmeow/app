"""Logical notifications (one per recipient and event) and their delivery
attempts (Batch F; FR-014). In-app read state lives on the notification;
each email attempt is a separate delivery row, so a retry never repeats the
business action that produced the notification."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core.models import BaseModel


class Category(models.TextChoices):
    APPLICATION = 'application', 'Application'
    VERIFICATION = 'verification', 'Verification'
    INTERVIEW = 'interview', 'Interview'
    RECOMMENDATION = 'recommendation', 'Decision'
    ROSTER_APPROVAL = 'roster_approval', 'Roster approval'
    SYSTEM = 'system', 'System'


class Notification(BaseModel):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name='notifications')
    application = models.ForeignKey('recruitment.Application', on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='notifications')
    interview = models.ForeignKey('committee.Interview', on_delete=models.PROTECT,
                                  null=True, blank=True, related_name='notifications')
    roster = models.ForeignKey('matrix.ApplicantRoster', on_delete=models.PROTECT,
                               null=True, blank=True, related_name='notifications')
    event_type = models.CharField(max_length=30, choices=Category.choices)
    event_key = models.CharField(max_length=200,
                                 help_text='Identifies the business event; one per recipient.')
    title = models.CharField(max_length=200)
    message = models.TextField()
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'notifications'
        ordering = ['-created_at']
        indexes = [models.Index(fields=['user', 'read_at'], name='notification_unread_idx')]
        constraints = [
            models.UniqueConstraint(fields=['user', 'event_key'],
                                    name='notification_unique_event'),
        ]

    def __str__(self):
        return self.title

    @property
    def is_read(self):
        return self.read_at is not None

    def target_url(self):
        """Where the recipient should go; chosen by what they are allowed to open."""
        from recruitment import application_selectors as access
        user = self.user
        if self.roster_id:
            return reverse('matrix:roster_detail', args=[self.roster_id])
        if self.application_id:
            application = self.application
            if access.is_owner(user, application):
                return reverse('recruitment:my_application', args=[application.pk])
            from recruitment import selectors
            if selectors.can_view(user, application.vacancy):
                name = 'recruitment:application_selection' if self.event_type in (
                    Category.INTERVIEW, Category.RECOMMENDATION) \
                    else 'recruitment:application_review'
                return reverse(name, args=[application.pk])
            from committee import selectors as committee_selectors
            if committee_selectors.can_view_candidate(user, application):
                return reverse('committee:candidate', args=[application.pk])
        if self.event_type == Category.INTERVIEW:
            return reverse('committee:my_candidates')
        return reverse('notifications:list')


class Channel(models.TextChoices):
    EMAIL = 'email', 'Email'


class DeliveryStatus(models.TextChoices):
    PENDING = 'pending', 'Pending'
    SENT = 'sent', 'Sent'
    FAILED = 'failed', 'Failed'


class NotificationDelivery(BaseModel):
    """One delivery attempt on one channel."""

    notification = models.ForeignKey(Notification, on_delete=models.PROTECT,
                                     related_name='deliveries')
    channel = models.CharField(max_length=20, choices=Channel.choices, default=Channel.EMAIL)
    attempt_number = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=DeliveryStatus.choices,
                              default=DeliveryStatus.PENDING)
    attempted_at = models.DateTimeField(default=timezone.now)
    delivered_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = 'notification_deliveries'
        ordering = ['notification', 'channel', 'attempt_number']
        constraints = [
            models.UniqueConstraint(fields=['notification', 'channel', 'attempt_number'],
                                    name='delivery_attempt_unique'),
            models.CheckConstraint(condition=Q(attempt_number__gt=0),
                                   name='delivery_attempt_positive'),
            models.CheckConstraint(
                condition=~Q(status='sent') | Q(delivered_at__isnull=False),
                name='delivery_sent_has_time'),
        ]
