"""Dashboard scope, metric snapshots, and report files (FR-016, FR-018).

Every figure is limited to vacancies the user may see (assignment-based for
HR and Department Heads, all for approvers). Report files are written once to
private storage under random names, hashed, and downloaded only by users who
can see every vacancy in the report's scope; downloads are audited.
"""

import hashlib
import secrets
from datetime import datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import roles
from audit import services as audit
from recruitment import selectors as vacancy_selectors
from recruitment.models import VacancyStatus

from . import metrics, reports
from .models import AnalyticsReport, RecruitmentMetric, ReportFormat, ReportType

ANALYTICS_ROLES = (roles.HR, roles.APPROVER, roles.DEPARTMENT_HEAD)


class ReportFailed(Exception):
    pass


def can_use(user):
    return roles.has_role(user, *ANALYTICS_ROLES)


def scope_vacancies(user, *, department=None, vacancy=None):
    qs = vacancy_selectors.visible_vacancies(user).exclude(status=VacancyStatus.DRAFT)
    if department is not None:
        qs = qs.filter(department=department)
    if vacancy is not None:
        qs = qs.filter(pk=vacancy.pk)
    return qs


def period_bounds(start_date=None, end_date=None):
    """Asia/Manila dates -> aware datetimes; the end date is inclusive."""
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(start_date, time.min), tz) if start_date \
        else None
    end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), tz) \
        if end_date else None
    if start and end and start >= end:
        raise ValidationError('The period must start before it ends.')
    return start, end


def describe_scope(user, vacancies, *, department=None, vacancy=None, start=None, end=None):
    ids = sorted(str(pk) for pk in vacancies.values_list('pk', flat=True))
    return {
        'schema_version': 1,
        'vacancy_ids': ids,
        'vacancy': vacancy.reference if vacancy else None,
        'department': department.code if department else None,
        'period_start': start.isoformat() if start else None,
        'period_end': end.isoformat() if end else None,
        'basis': 'all vacancies' if roles.has_role(user, roles.APPROVER)
                 else 'assigned vacancies',
    }


def scope_label(scope):
    parts = [scope.get('basis') or 'permitted vacancies']
    if scope.get('department'):
        parts.append(f"department {scope['department']}")
    if scope.get('vacancy'):
        parts.append(f"vacancy {scope['vacancy']}")
    start, end = scope.get('period_start'), scope.get('period_end')
    if start or end:
        fmt = lambda v: timezone.localtime(datetime.fromisoformat(v)).strftime('%b %d, %Y')
        last = (timezone.localtime(datetime.fromisoformat(end)) - timedelta(days=1)) \
            .strftime('%b %d, %Y') if end else 'today'
        parts.append(f"submitted {fmt(start) if start else 'from the start'} – {last}")
    else:
        parts.append('all submission dates')
    return '; '.join(parts)


# --------------------------------------------------------------------------
# Snapshots
# --------------------------------------------------------------------------

@transaction.atomic
def save_snapshot(actor, vacancies, scope, *, vacancy=None, start=None, end=None):
    if not can_use(actor):
        raise PermissionDenied
    values = metrics.compute(vacancies, period_start=start, period_end=end)
    snapshot = RecruitmentMetric.objects.create(
        vacancy=vacancy, period_start=start, period_end=end,
        definition_version=values['definition_version'], scope=scope,
        unique_applicants=values['unique_applicants'],
        submitted_applications=values['submitted_applications'],
        shortlisted_count=values['shortlisted_count'],
        scheduled_interviews=values['scheduled_interviews'],
        completed_interviews=values['completed_interviews'],
        reviewed_applications=values['reviewed_applications'],
        recommended_count=values['recommended_count'],
        time_to_fill_seconds=values['time_to_fill_seconds'],
        stage_durations={'schema_version': 1, 'stages': values['stage_durations'],
                         'status_distribution': values['status_distribution'],
                         'canceled_interviews': values['canceled_interviews'],
                         'hired_count': values['hired_count'],
                         'current_evaluations': values['current_evaluations']},
        computed_at=values['computed_at'], computed_by=actor)
    audit.record('metrics.snapshot_saved', entity=snapshot, actor=actor,
                 changes={'scope': scope_label(scope)})
    return snapshot


def covers(user, scope):
    """True if the user can see every vacancy in the scope."""
    ids = set(scope.get('vacancy_ids', []))
    if not ids:
        return can_use(user)
    visible = {str(pk) for pk in vacancy_selectors.visible_vacancies(user)
               .filter(pk__in=ids).values_list('pk', flat=True)}
    return can_use(user) and ids <= visible


def visible_snapshots(user):
    return [s for s in RecruitmentMetric.objects.select_related('computed_by', 'vacancy')[:200]
            if covers(user, s.scope)]


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def report_root():
    return Path(settings.REPORT_STORAGE_ROOT)


def report_path(report):
    root = report_root().resolve()
    path = (root / report.storage_key).resolve()
    if root not in path.parents:
        raise PermissionDenied('Invalid report location.')
    return path


def generate_report(actor, report_type, fmt, vacancies, scope, *, start=None, end=None):
    if not can_use(actor):
        raise PermissionDenied
    if report_type not in ReportType.values or fmt not in ReportFormat.values:
        raise ValidationError('Choose a report type and format.')
    now = timezone.now()
    content = reports.build(report_type, fmt, vacancies, start=start, end=end, meta={
        'scope': scope_label(scope), 'generated_by': actor.full_name or actor.username,
        'generated_at': timezone.localtime(now).strftime('%B %d, %Y %I:%M %p'),
        'definition_version': metrics.DEFINITION_VERSION})
    key = f'{now:%Y/%m}/{secrets.token_hex(16)}.{fmt}'
    root = report_root()
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, 'xb') as handle:  # never overwrite
            handle.write(content)
        with transaction.atomic():
            report = AnalyticsReport.objects.create(
                report_type=report_type, scope=scope,
                definition_version=metrics.DEFINITION_VERSION, generated_by=actor,
                generated_at=now, storage_key=key, sha256=hashlib.sha256(content).hexdigest())
            audit.record('report.generated', entity=report, actor=actor,
                         changes={'report_type': report_type, 'format': fmt,
                                  'scope': scope_label(scope)})
    except Exception as exc:
        path.unlink(missing_ok=True)
        audit.record('report.generation_failed', actor=actor,
                     entity_type='analytics.AnalyticsReport',
                     changes={'report_type': report_type, 'format': fmt,
                              'error': type(exc).__name__})
        raise ReportFailed('The report could not be generated. Please try again.') from exc
    return report


def visible_reports(user):
    return [r for r in AnalyticsReport.objects.select_related('generated_by')[:200]
            if covers(user, r.scope)]


def can_download(user, report):
    return covers(user, report.scope)


def record_download(user, report):
    audit.record('report.downloaded', entity=report, actor=user,
                 changes={'report_type': report.report_type, 'format': report.format})
