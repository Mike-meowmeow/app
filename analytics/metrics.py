"""Recruitment metric definitions (FR-018), version metrics-v1.

The live dashboard, saved snapshots, and the statistics report all call
`compute()`, so they always use the same definitions. Counts come from
recorded submissions, workflow history, interviews, evaluations, and
decisions. A rate with a zero denominator is None (shown as Not applicable).
"""

from collections import Counter, defaultdict

from django.db.models import Count, Max
from django.utils import timezone

from committee.models import (AttendanceStatus, EvaluationStatus, Interview, InterviewEvaluation,
                              InterviewStatus)
from recruitment.models import (Application, ApplicationStatus, DecisionOutcome, DecisionType,
                                RecruitmentDecision, VacancyStatus, WorkflowLog)

DEFINITION_VERSION = 'metrics-v1'
S = ApplicationStatus

DEFINITIONS = [
    ('Scope', 'Applications first submitted within the period (Asia/Manila dates, end date '
              'inclusive) for vacancies you are permitted to see, narrowed by the chosen '
              'department or vacancy. Drafts are never counted.'),
    ('Submitted applications', 'Applications in scope. Withdrawn, not-selected, and closed '
                               'applications stay counted; they were submitted.'),
    ('Unique applicants', 'Distinct people among the submitted applications (one person may '
                          'apply to several vacancies).'),
    ('Shortlisting rate', 'Applications ever shortlisted (a Shortlisted status in their history, '
                          'even if later concluded) ÷ submitted applications.'),
    ('Interview completion rate', 'Interviews held with the applicant attending ÷ interviews '
                                  'scheduled. Canceled interviews are excluded from both; '
                                  'no-shows count as scheduled but not completed.'),
    ('Recommendation rate', 'Applications with a current committee recommendation of '
                            'Recommended ÷ applications with a recorded committee recommendation '
                            '(completed committee review).'),
    ('Evaluations', 'Submitted evaluations that have not been replaced by a submitted '
                    'amendment. Drafts and superseded versions are excluded.'),
    ('Time to fill', 'From vacancy publication to the latest recorded appointment of a vacancy '
                     'whose positions are all filled. Unfilled vacancies have no value; for '
                     'several vacancies the mean of filled ones is shown.'),
    ('Stage durations', 'Mean days between first entering one status and first entering the '
                        'next, over applications that reached both.'),
]

STAGES = [(S.SUBMITTED, S.UNDER_REVIEW), (S.UNDER_REVIEW, S.SHORTLISTED),
          (S.SHORTLISTED, S.FOR_INTERVIEW), (S.FOR_INTERVIEW, S.RECOMMENDED),
          (S.RECOMMENDED, S.HIRED)]


def rate(numerator, denominator):
    return None if not denominator else numerator / denominator


def scoped_applications(vacancies, period_start=None, period_end=None):
    qs = Application.objects.filter(vacancy__in=vacancies, submitted_at__isnull=False)
    if period_start:
        qs = qs.filter(submitted_at__gte=period_start)
    if period_end:
        qs = qs.filter(submitted_at__lt=period_end)
    return qs


def time_to_fill(vacancies):
    """{vacancy_id: seconds} for filled vacancies in scope."""
    filled = {v.pk: v for v in vacancies.filter(status=VacancyStatus.FILLED,
                                                posted_at__isnull=False)}
    latest = RecruitmentDecision.objects.filter(
        application__vacancy_id__in=filled, decision_type=DecisionType.APPOINTMENT,
        superseded_by__isnull=True).order_by().values('application__vacancy_id') \
        .annotate(last=Max('appointment_at'))
    result = {}
    for row in latest:
        vacancy = filled[row['application__vacancy_id']]
        result[vacancy.pk] = max(0, int((row['last'] - vacancy.posted_at).total_seconds()))
    return result


def stage_durations(application_ids):
    first = defaultdict(dict)
    for app_id, status, when in WorkflowLog.objects.filter(
            application_id__in=application_ids).order_by('occurred_at') \
            .values_list('application_id', 'to_status', 'occurred_at'):
        first[app_id].setdefault(status, when)
    result = []
    for a, b in STAGES:
        spans = [(entered[b] - entered[a]).total_seconds() / 86400
                 for entered in first.values() if a in entered and b in entered
                 and entered[b] >= entered[a]]
        result.append({'from': a, 'to': b, 'label': f'{S(a).label} → {S(b).label}',
                       'applications': len(spans),
                       'mean_days': round(sum(spans) / len(spans), 2) if spans else None})
    return result


def compute(vacancies, *, period_start=None, period_end=None):
    """All metric values for the vacancies queryset and period."""
    apps = scoped_applications(vacancies, period_start, period_end)
    ids = list(apps.values_list('pk', flat=True))
    submitted = len(ids)
    unique = apps.order_by().values('applicant_id').distinct().count()
    shortlisted = WorkflowLog.objects.filter(application_id__in=ids, to_status=S.SHORTLISTED) \
        .order_by().values('application_id').distinct().count()

    interviews = Interview.objects.filter(application_id__in=ids)
    canceled = interviews.filter(status=InterviewStatus.CANCELED).count()
    scheduled = interviews.exclude(status=InterviewStatus.CANCELED).count()
    completed = interviews.filter(status=InterviewStatus.COMPLETED,
                                  attendance_status=AttendanceStatus.ATTENDED).count()
    no_shows = interviews.filter(attendance_status=AttendanceStatus.NO_SHOW).count()

    recommendations = RecruitmentDecision.objects.filter(
        application_id__in=ids, decision_type=DecisionType.COMMITTEE_RECOMMENDATION,
        superseded_by__isnull=True)
    reviewed = recommendations.order_by().values('application_id').distinct().count()
    recommended = recommendations.filter(outcome=DecisionOutcome.RECOMMENDED) \
        .order_by().values('application_id').distinct().count()

    evaluations = [e for e in InterviewEvaluation.objects.filter(
        application_id__in=ids, status=EvaluationStatus.SUBMITTED).select_related(
        'superseded_by') if e.is_current]

    distribution = Counter(dict(apps.order_by().values_list('status').annotate(n=Count('pk'))))
    trend = Counter(timezone.localtime(t).strftime('%Y-%m')
                    for t in apps.values_list('submitted_at', flat=True))
    fills = time_to_fill(vacancies)
    mean_fill = int(sum(fills.values()) / len(fills)) if fills else None
    now = timezone.now()
    active = vacancies.filter(status=VacancyStatus.PUBLISHED, opens_at__lte=now,
                              deadline_at__gt=now).count()
    return {
        'definition_version': DEFINITION_VERSION,
        'computed_at': now,
        'submitted_applications': submitted,
        'unique_applicants': unique,
        'shortlisted_count': shortlisted,
        'shortlisting_rate': rate(shortlisted, submitted),
        'scheduled_interviews': scheduled,
        'completed_interviews': completed,
        'canceled_interviews': canceled,
        'no_show_interviews': no_shows,
        'interview_completion_rate': rate(completed, scheduled),
        'reviewed_applications': reviewed,
        'recommended_count': recommended,
        'recommendation_rate': rate(recommended, reviewed),
        'hired_count': distribution.get(S.HIRED, 0),
        'withdrawn_count': distribution.get(S.WITHDRAWN, 0),
        'current_evaluations': len(evaluations),
        'active_vacancies': active,
        'time_to_fill_seconds': mean_fill,
        'filled_vacancies': len(fills),
        'status_distribution': [(S(s).label, distribution.get(s, 0)) for s in S.values
                                if s != S.DRAFT],
        'trend': sorted(trend.items()),
        'stage_durations': stage_durations(ids),
    }


def format_rate(value):
    return 'Not applicable' if value is None else f'{value:.1%}'


def format_duration(seconds):
    if seconds is None:
        return 'Not applicable'
    days = seconds / 86400
    return f'{days:.1f} days'
