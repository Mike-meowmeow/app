from django.contrib import admin, messages
from django.contrib.admin import helpers
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.template.response import TemplateResponse

from . import position_services as catalog
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
    """View-only, plus actions for System Administrators: deactivate,
    reactivate, and delete never-used positions with a reason. The actions go
    through position_services, so they follow the same rules and are audited."""

    list_display = ('title', 'is_active', 'vacancy_count', 'updated_at')
    list_filter = ('is_active',)
    search_fields = ('title',)
    actions = ('deactivate_positions', 'reactivate_positions', 'delete_positions')

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(vacancies_used=Count('vacancies'))

    @admin.display(description='Vacancies created from it', ordering='vacancies_used')
    def vacancy_count(self, obj):
        return obj.vacancies_used

    def has_manage_catalog_permission(self, request):
        return catalog.can_administer_catalog(request.user)

    def _set_active(self, request, queryset, active):
        changed = [p.title for p in queryset if p.is_active != active]
        for position in queryset:
            if position.is_active != active:
                catalog.set_position_active(request.user, position, active,
                                            reason='Changed in the Django admin')
        word = 'reactivated' if active else 'deactivated'
        if changed:
            self.message_user(request, f'{len(changed)} position(s) {word}: '
                                       f'{", ".join(changed)}.', messages.SUCCESS)
        else:
            self.message_user(request, f'The selected positions were already {word}.',
                              messages.INFO)

    @admin.action(description='Deactivate selected positions', permissions=['manage_catalog'])
    def deactivate_positions(self, request, queryset):
        self._set_active(request, queryset, False)

    @admin.action(description='Reactivate selected positions', permissions=['manage_catalog'])
    def reactivate_positions(self, request, queryset):
        self._set_active(request, queryset, True)

    @admin.action(description='Delete selected positions (only never-used ones)',
                  permissions=['manage_catalog'])
    def delete_positions(self, request, queryset):
        positions = list(queryset)
        deletable = [p for p in positions if not p.vacancies_used]
        used = [p for p in positions if p.vacancies_used]
        reason = request.POST.get('reason', '').strip()
        error = ''
        if request.POST.get('confirm') and deletable:
            if not reason:
                error = 'Give a reason for deleting.'
            else:
                deleted = []
                for position in deletable:
                    title = position.title
                    try:
                        catalog.delete_position(request.user, position, reason=reason)
                    except ValidationError as exc:
                        self.message_user(request, ' '.join(exc.messages), messages.ERROR)
                    else:
                        deleted.append(title)
                if deleted:
                    self.message_user(request, f'{len(deleted)} position(s) deleted: '
                                               f'{", ".join(deleted)}.', messages.SUCCESS)
                if used:
                    self.message_user(request, 'Not deleted because vacancies were created from '
                                               'them (deactivate them instead): '
                                      + ', '.join(p.title for p in used) + '.', messages.WARNING)
                return None
        return TemplateResponse(request, 'admin/recruitment/position/delete_with_reason.html', {
            **self.admin_site.each_context(request),
            'title': 'Delete positions', 'opts': self.model._meta,
            'deletable': deletable, 'used': used, 'positions': positions,
            'action_checkbox_name': helpers.ACTION_CHECKBOX_NAME,
            'reason': reason, 'error': error,
        })


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
