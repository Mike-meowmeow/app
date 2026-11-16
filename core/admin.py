from django.contrib import admin

from .models import Department, RecruitmentType


class ReferenceDataAdmin(admin.ModelAdmin):
    """Reference data (FR-026). Deactivate instead of deleting, so historical
    vacancies and applications keep their references."""

    list_display = ('code', 'name', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')

    def has_delete_permission(self, request, obj=None):
        return False


admin.site.register(Department, ReferenceDataAdmin)
admin.site.register(RecruitmentType, ReferenceDataAdmin)
