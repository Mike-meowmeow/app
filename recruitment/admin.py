from django.contrib import admin

from .models import (Position, RecruitmentDecision, Vacancy, VacancyAssignment,
                     VacancyRequirementVersion)


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


@admin.register(Position)
class PositionAdmin(ReadOnlyAdmin):
    list_display = ('title', 'is_active', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('title',)


@admin.register(VacancyRequirementVersion)
class VacancyRequirementVersionAdmin(ReadOnlyAdmin):
    list_display = ('vacancy', 'version', 'published_at', 'published_by')


@admin.register(VacancyAssignment)
class VacancyAssignmentAdmin(ReadOnlyAdmin):
    list_display = ('vacancy', 'user', 'assignment_role', 'ended_at')
    list_filter = ('assignment_role',)


from .models import Application, ApplicationSnapshot, WorkflowLog  # noqa: E402


@admin.register(Application)
class ApplicationAdmin(ReadOnlyAdmin):
    list_display = ('__str__', 'vacancy', 'status', 'submitted_at', 'revision')
    list_filter = ('status',)


@admin.register(ApplicationSnapshot)
class ApplicationSnapshotAdmin(ReadOnlyAdmin):
    list_display = ('application', 'version', 'snapshot_type', 'captured_at')


@admin.register(WorkflowLog)
class WorkflowLogAdmin(ReadOnlyAdmin):
    list_display = ('application', 'from_status', 'to_status', 'actor', 'occurred_at')


@admin.register(RecruitmentDecision)
class RecruitmentDecisionAdmin(ReadOnlyAdmin):
    list_display = ('application', 'decision_type', 'outcome', 'decided_by', 'decision_at',
                    'released_at')
    list_filter = ('decision_type', 'outcome')
