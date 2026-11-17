"""Backup and restore monitoring (Batch F operations tables; FR-020).

These rows record what the backup and restore-check commands did. They do
not implement scheduling, off-site storage, or encryption, which belong to
the server's operational procedure (see deploy/RECOVERY.md)."""

from django.conf import settings
from django.db import models

from core.models import BaseModel


class RunStatus(models.TextChoices):
    RUNNING = 'running', 'Running'
    COMPLETED = 'completed', 'Completed'
    FAILED = 'failed', 'Failed'


class BackupRun(BaseModel):
    """One consistent backup set: database dump, then private files."""

    backup_set_id = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=20, choices=RunStatus.choices,
                              default=RunStatus.RUNNING)
    database_reference = models.CharField(max_length=255, blank=True)
    documents_reference = models.CharField(max_length=255, blank=True)
    exports_reference = models.CharField(max_length=255, blank=True)
    manifest = models.JSONField(default=dict, blank=True)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     null=True, blank=True, related_name='+')

    class Meta:
        db_table = 'backup_runs'
        ordering = ['-started_at']

    def __str__(self):
        return f'Backup {self.backup_set_id} ({self.get_status_display()})'


class RestoreRun(BaseModel):
    """A restore of one backup set into an isolated database and folder,
    with the verification results."""

    backup_run = models.ForeignKey(BackupRun, on_delete=models.PROTECT,
                                   related_name='restore_runs')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     related_name='+')
    status = models.CharField(max_length=20, choices=RunStatus.choices,
                              default=RunStatus.RUNNING)
    target_database = models.CharField(max_length=64)
    started_at = models.DateTimeField()
    completed_at = models.DateTimeField(null=True, blank=True)
    verification_result = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        db_table = 'restore_runs'
        ordering = ['-started_at']

    def __str__(self):
        return f'Restore check of {self.backup_run.backup_set_id} ({self.get_status_display()})'
