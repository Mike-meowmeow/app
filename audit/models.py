from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import AppendOnlyModel


class AuditLog(AppendOnlyModel):
    """Protected business event history.

    The optional job link to ai_jobs (Stage 10) marks events caused by or about
    an AI job; manual events keep it null.
    """

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                              null=True, blank=True, related_name='audit_events')
    job = models.ForeignKey('ai_processing.AIJob', on_delete=models.PROTECT, null=True,
                            blank=True, related_name='audit_events')
    action = models.CharField(max_length=64)
    entity_type = models.CharField(max_length=100)
    entity_id = models.CharField(max_length=64)
    changes = models.JSONField(default=dict, blank=True)
    reason = models.TextField(blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    occurred_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'audit_logs'
        ordering = ['-occurred_at']
        indexes = [
            models.Index(fields=['entity_type', 'entity_id', 'occurred_at'],
                         name='audit_entity_time_idx'),
            models.Index(fields=['occurred_at'], name='audit_occurred_idx'),
        ]

    def __str__(self):
        return f'{self.occurred_at:%Y-%m-%d %H:%M} {self.action} {self.entity_type}:{self.entity_id}'
