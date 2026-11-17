"""Who can see and act on applications (FR-002).

- Applicants: only their own applications (drafts included).
- Staff: submitted applications of vacancies they can view (assignment-based;
  approvers see all). Drafts are private to the applicant.
- Review actions: HR with an active HR assignment on the vacancy.
"""

from django.db.models import Q

from . import selectors
from .models import Application, ApplicationStatus


def own_applications(user):
    return Application.objects.select_related('vacancy', 'vacancy__department') \
        .filter(applicant__user=user)


def visible_applications(user):
    return Application.objects.select_related('vacancy', 'applicant__user').filter(
        vacancy__in=selectors.visible_vacancies(user).values('pk')
    ).exclude(status=ApplicationStatus.DRAFT)


def is_owner(user, application):
    return user.is_authenticated and application.applicant.user_id == user.pk


def can_view(user, application):
    if is_owner(user, application):
        return True
    if application.status == ApplicationStatus.DRAFT:
        return False
    if selectors.can_view(user, application.vacancy):
        return True
    # Committee members: shortlisted candidates of their vacancy, unless conflicted.
    from committee import selectors as committee_selectors
    return committee_selectors.can_view_candidate(user, application)


def can_review(user, application):
    return application.status != ApplicationStatus.DRAFT and \
        selectors.can_manage(user, application.vacancy)


def _current_facts(**filters):
    from .models import ApplicationFact, FactReviewStatus
    return ApplicationFact.objects.exclude(
        review_status__in=(FactReviewStatus.SUPERSEDED, FactReviewStatus.REJECTED)
    ).filter(**filters).values('application_id')


def search(queryset, *, q='', status='', vacancy=None, department=None, degree_level='',
           outcome=''):
    """Combined filters (FR-015). Permission limits come from `queryset`; these
    filters never change official roster membership."""
    if vacancy is not None:
        queryset = queryset.filter(vacancy=vacancy)
    if department is not None:
        queryset = queryset.filter(vacancy__department=department)
    if degree_level:
        queryset = queryset.filter(pk__in=_current_facts(
            fact_type='education', value__degree_level=degree_level))
    if outcome == 'not_assessed':
        queryset = queryset.exclude(pk__in=_current_facts(fact_type='qualification_assessment'))
    elif outcome:
        queryset = queryset.filter(pk__in=_current_facts(
            fact_type='qualification_assessment', value__outcome=outcome))
    return _search_text(queryset, q=q, status=status)


def _search_text(queryset, *, q='', status=''):
    if q:
        q = q.strip()
        term = (Q(applicant__user__first_name__icontains=q)
                | Q(applicant__user__last_name__icontains=q)
                | Q(applicant__user__email__icontains=q)
                | Q(facts__value__program__icontains=q))
        ref = q.upper().removeprefix('APP-').replace('-', '')
        if len(ref) >= 4 and all(c in '0123456789ABCDEF' for c in ref):
            term |= Q(id__startswith=ref.lower())
        queryset = queryset.filter(term).distinct()
    if status:
        queryset = queryset.filter(status=status)
    return queryset
