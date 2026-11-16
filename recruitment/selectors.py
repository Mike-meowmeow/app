"""Who can see and manage which vacancies (FR-002 record-level access).

- HR Personnel and Department Heads: only vacancies with an active explicit
  assignment for them. Department membership alone grants nothing.
- HR Head / Approver: every vacancy, for oversight (read-only configuration).
- Applicants and the public: published vacancies only.
"""

from django.db.models import Q
from django.utils import timezone

from accounts import roles

from .models import AssignmentRole, Vacancy, VacancyAssignment, VacancyStatus


def visible_vacancies(user):
    qs = Vacancy.objects.select_related('department', 'recruitment_type')
    if roles.has_role(user, roles.APPROVER):
        return qs
    allowed = []
    if roles.has_role(user, roles.HR):
        allowed.append(AssignmentRole.HR)
    if roles.has_role(user, roles.DEPARTMENT_HEAD):
        allowed.append(AssignmentRole.DEPARTMENT_HEAD)
    if not allowed:
        return qs.none()
    ids = VacancyAssignment.objects.filter(user=user, ended_at__isnull=True,
                                           assignment_role__in=allowed) \
        .values('vacancy_id')
    return qs.filter(pk__in=ids)


def can_view(user, vacancy):
    return visible_vacancies(user).filter(pk=vacancy.pk).exists()


def can_manage(user, vacancy):
    """Edit configuration and lifecycle: HR with an active HR assignment."""
    return roles.has_role(user, roles.HR) and VacancyAssignment.objects.filter(
        vacancy=vacancy, user=user, assignment_role=AssignmentRole.HR,
        ended_at__isnull=True).exists()


def can_assign_staff(user, vacancy):
    return can_manage(user, vacancy) or roles.has_role(user, roles.APPROVER)


def public_vacancies():
    """Published vacancies whose deadline has not passed (open or upcoming)."""
    return Vacancy.objects.select_related('department', 'recruitment_type').filter(
        status=VacancyStatus.PUBLISHED, deadline_at__gt=timezone.now()
    ).order_by('deadline_at')


def search(queryset, *, q='', department=None, recruitment_type=None, status=''):
    if q:
        q = q.strip()
        term = Q(position_title__icontains=q) | Q(description__icontains=q)
        ref = q.upper().removeprefix('VAC-').replace('-', '')
        if len(ref) >= 4 and all(c in '0123456789ABCDEF' for c in ref):
            term |= Q(id__startswith=ref.lower())
        queryset = queryset.filter(term)
    if department:
        queryset = queryset.filter(department=department)
    if recruitment_type:
        queryset = queryset.filter(recruitment_type=recruitment_type)
    if status:
        queryset = queryset.filter(status=status)
    return queryset
