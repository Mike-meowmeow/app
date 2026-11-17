"""Metric snapshots and generated reports (Batch F; FR-016, FR-018).

Metrics are timestamped snapshots, never one mutable row per vacancy; the
definition version and scope identify how they were calculated. Reports keep
their scope, generator, time, and the private file they produced."""

from django.conf import settings
from django.db import models
from django.db.models import Q

from core.models import AppendOnlyModel


class RecruitmentMetric(AppendOnlyModel):
    vacancy = models.ForeignKey('recruitment.Vacancy', on_delete=models.PROTECT, null=True,
                                blank=True, related_name='metrics',
                                help_text='Set when the scope is exactly one vacancy.')
    period_start = models.DateTimeField(null=True, blank=True)
    period_end = models.DateTimeField(null=True, blank=True)
    definition_version = models.CharField(max_length=30)
    scope = models.JSONField()
    unique_applicants = models.PositiveIntegerField()
    submitted_applications = models.PositiveIntegerField()
    shortlisted_count = models.PositiveIntegerField()
    scheduled_interviews = models.PositiveIntegerField()
    completed_interviews = models.PositiveIntegerField()
    reviewed_applications = models.PositiveIntegerField()
    recommended_count = models.PositiveIntegerField()
    time_to_fill_seconds = models.BigIntegerField(null=True, blank=True)
    stage_durations = models.JSONField(default=dict, blank=True)
    computed_at = models.DateTimeField()
    computed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                    null=True, blank=True, related_name='+')

    class Meta:
        db_table = 'recruitment_metrics'
        ordering = ['-computed_at']
        constraints = [
            models.CheckConstraint(
                condition=Q(period_start__isnull=True) | Q(period_end__isnull=True)
                | Q(period_start__lt=models.F('period_end')),
                name='metric_period_order'),
            models.CheckConstraint(
                condition=Q(time_to_fill_seconds__isnull=True) | Q(time_to_fill_seconds__gte=0),
                name='metric_time_to_fill_nonnegative'),
        ]


class ReportType(models.TextChoices):
    APPLICANTS = 'applicants', 'Applicant summary'
    VACANCIES = 'vacancies', 'Vacancy summary'
    DECISIONS = 'decisions', 'Recorded decisions'
    STATISTICS = 'statistics', 'Recruitment statistics'


class ReportFormat(models.TextChoices):
    XLSX = 'xlsx', 'Excel'
    PDF = 'pdf', 'PDF'


class AnalyticsReport(AppendOnlyModel):
    report_type = models.CharField(max_length=20, choices=ReportType.choices)
    scope = models.JSONField()
    definition_version = models.CharField(max_length=30)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                                     related_name='+')
    generated_at = models.DateTimeField()
    storage_key = models.CharField(max_length=200, unique=True)
    sha256 = models.CharField(max_length=64)

    class Meta:
        db_table = 'analytics_reports'
        ordering = ['-generated_at']

    def __str__(self):
        return f'{self.get_report_type_display()} ({self.generated_at:%Y-%m-%d %H:%M})'

    @property
    def format(self):
        return self.storage_key.rsplit('.', 1)[-1]

    @property
    def file_name(self):
        return f'{self.report_type}-{self.generated_at:%Y%m%d-%H%M}.{self.format}'
