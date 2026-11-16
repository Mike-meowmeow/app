"""Committee records are view-only in the admin; changes go through services
so that conflicts, schedule history, and evaluation versions are preserved."""

from django.contrib import admin

from .models import (Committee, CommitteeConflict, CommitteeMember, Interview, InterviewEvaluation,
                     InterviewEvent)


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Committee)
class CommitteeAdmin(ReadOnlyAdmin):
    list_display = ('name', 'vacancy', 'is_active', 'created_at')


@admin.register(CommitteeMember)
class CommitteeMemberAdmin(ReadOnlyAdmin):
    list_display = ('user', 'committee', 'member_role', 'ended_at')


@admin.register(CommitteeConflict)
class CommitteeConflictAdmin(ReadOnlyAdmin):
    list_display = ('committee_member', 'application', 'status', 'declared_at', 'resolved_at')
    list_filter = ('status',)


@admin.register(Interview)
class InterviewAdmin(ReadOnlyAdmin):
    list_display = ('application', 'starts_at', 'mode', 'status', 'attendance_status')
    list_filter = ('status', 'attendance_status')


@admin.register(InterviewEvent)
class InterviewEventAdmin(ReadOnlyAdmin):
    list_display = ('interview', 'event_type', 'actor', 'occurred_at')


@admin.register(InterviewEvaluation)
class InterviewEvaluationAdmin(ReadOnlyAdmin):
    list_display = ('application', 'committee_member', 'interview', 'version', 'status',
                    'submitted_at')
