from django.contrib import admin

from recruitment.admin import ReadOnlyAdmin

from .models import ApplicantRoster, RosterTemplate


@admin.register(RosterTemplate)
class RosterTemplateAdmin(ReadOnlyAdmin):
    """View-only: templates are registered with manage.py register_roster_template
    and approved in the application, so files are validated and audited."""
    list_display = ('name', 'recruitment_type', 'version', 'status', 'approved_at')
    list_filter = ('status', 'recruitment_type')


@admin.register(ApplicantRoster)
class ApplicantRosterAdmin(ReadOnlyAdmin):
    list_display = ('vacancy', 'version', 'status', 'requires_regeneration', 'approved_at',
                    'released_at')
    list_filter = ('status',)
