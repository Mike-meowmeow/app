from datetime import timedelta

from django.conf import settings
from django.shortcuts import render
from django.utils import timezone

from accounts import roles
from accounts.access import role_required

from .models import BackupRun, RestoreRun, RunStatus


@role_required(roles.SYSTEM_ADMIN)
def overview(request):
    """Backup and restore monitoring for System Administrators (FR-020)."""
    backups = BackupRun.objects.select_related('requested_by')[:50]
    restores = RestoreRun.objects.select_related('backup_run', 'requested_by')[:20]
    last_good = BackupRun.objects.filter(status=RunStatus.COMPLETED).first()
    max_age = timedelta(hours=settings.BACKUP_MAX_AGE_HOURS)
    stale = last_good is None or timezone.now() - last_good.completed_at > max_age
    return render(request, 'operations/overview.html', {
        'backups': backups, 'restores': restores, 'last_good': last_good, 'stale': stale,
        'max_age_hours': settings.BACKUP_MAX_AGE_HOURS,
        'last_restore': RestoreRun.objects.filter(status=RunStatus.COMPLETED).first()})
