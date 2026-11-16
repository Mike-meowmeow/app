from django.contrib import admin

from recruitment.admin import ReadOnlyAdmin

from .models import Applicant


@admin.register(Applicant)
class ApplicantAdmin(ReadOnlyAdmin):
    """View-only; applicants maintain their own profiles."""

    list_display = ('__str__', 'contact_no', 'updated_at')
    search_fields = ('user__first_name', 'user__last_name', 'user__email')
