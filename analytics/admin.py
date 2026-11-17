from django.contrib import admin

from .models import AnalyticsReport, RecruitmentMetric


class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(RecruitmentMetric)
class RecruitmentMetricAdmin(ReadOnlyAdmin):
    list_display = ('computed_at', 'vacancy', 'submitted_applications', 'definition_version')


@admin.register(AnalyticsReport)
class AnalyticsReportAdmin(ReadOnlyAdmin):
    list_display = ('report_type', 'generated_by', 'generated_at', 'storage_key')
