from django.contrib import admin

from .models import Vacancy, VacancyAssignment, VacancyRequirementVersion


class ReadOnlyAdmin(admin.ModelAdmin):
    """View-only: vacancy changes go through the application so they are
    authorized, versioned, and audited."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Vacancy)
class VacancyAdmin(ReadOnlyAdmin):
    list_display = ('position_title', 'department', 'status', 'deadline_at')
    list_filter = ('status', 'department', 'recruitment_type')
    search_fields = ('position_title',)


@admin.register(VacancyRequirementVersion)
class VacancyRequirementVersionAdmin(ReadOnlyAdmin):
    list_display = ('vacancy', 'version', 'published_at', 'published_by')


@admin.register(VacancyAssignment)
class VacancyAssignmentAdmin(ReadOnlyAdmin):
    list_display = ('vacancy', 'user', 'assignment_role', 'ended_at')
    list_filter = ('assignment_role',)
