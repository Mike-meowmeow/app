from django.contrib import admin

from .models import BackupRun, RestoreRun


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(BackupRun)
class BackupRunAdmin(ReadOnlyAdmin):
    list_display = ('backup_set_id', 'status', 'started_at', 'completed_at')
    list_filter = ('status',)


@admin.register(RestoreRun)
class RestoreRunAdmin(ReadOnlyAdmin):
    list_display = ('backup_run', 'status', 'requested_by', 'started_at', 'completed_at')
    list_filter = ('status',)
