"""Publication rules of the CSC model merit system for SUC faculty
(CSC MC No. 19, s. 2005, Chapter 4 II.A; RA 7041).

- A vacancy is posted for at least RECRUITMENT_MIN_POSTING_DAYS calendar days
  (10). Publishing is refused when the application window is shorter.
- A publication is valid for RECRUITMENT_PUBLICATION_VALID_MONTHS (6) and the
  vacancy must be filled within RECRUITMENT_FILL_LIMIT_MONTHS (9) of
  publication, otherwise it is published again. These two only warn.

The values are settings because later CSC rules or EVSU's approved Merit
Selection Plan may differ; HR confirms them.
"""

import calendar
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import VacancyStatus


def min_posting_days():
    return settings.RECRUITMENT_MIN_POSTING_DAYS


def add_months(value, months):
    """The same day and time `months` later (or earlier, when negative); the
    31st becomes the month's last day."""
    month = value.month - 1 + months
    year, month = value.year + month // 12, month % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def posting_problem(opens_at, deadline_at, *, posted_at=None, now=None):
    """A message when applicants would have fewer than the minimum days to
    apply, counted from the later of the opening date and the publication
    (now for a vacancy not yet published); otherwise None."""
    if not (opens_at and deadline_at):
        return None
    start = max(opens_at, posted_at or now or timezone.now())
    days = min_posting_days()
    if deadline_at - start >= timedelta(days=days):
        return None
    return (f'The vacancy must be open for applications for at least {days} calendar days '
            f'(CSC merit system rules); set the deadline on or after '
            f'{timezone.localtime(start + timedelta(days=days)):%b %d, %Y %I:%M %p}.')


def publication_warning(vacancy, now=None):
    """(level, message) for a published or closed vacancy whose publication is
    older than the validity or fill limit; None otherwise. Level 'fill_limit'
    is past 9 months, 'validity' past 6."""
    if not vacancy.posted_at or vacancy.status not in (VacancyStatus.PUBLISHED,
                                                       VacancyStatus.CLOSED):
        return None
    now = now or timezone.now()
    posted = timezone.localtime(vacancy.posted_at)
    valid_until = add_months(posted, settings.RECRUITMENT_PUBLICATION_VALID_MONTHS)
    fill_by = add_months(posted, settings.RECRUITMENT_FILL_LIMIT_MONTHS)
    on = f'{posted:%b %d, %Y}'
    if now >= fill_by:
        return ('fill_limit',
                f'Published on {on}, more than {settings.RECRUITMENT_FILL_LIMIT_MONTHS} months '
                f'ago. CSC rules require publishing the vacancy again before an appointment '
                f'is issued.')
    if now >= valid_until:
        return ('validity',
                f'Published on {on}, more than {settings.RECRUITMENT_PUBLICATION_VALID_MONTHS} '
                f'months ago. The publication still counts only if screening started within '
                f'{settings.RECRUITMENT_PUBLICATION_VALID_MONTHS} months, and the vacancy must '
                f'be filled by {fill_by:%b %d, %Y}.')
    return None
