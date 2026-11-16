"""Who can see committee work (FR-002, FR-010).

- Committee members (PSB Member / Department Head with an active membership
  on an active committee) see the shortlisted applications of that committee's
  vacancy only, never other vacancies, and lose access to an application
  while they have an open or recused conflict for it.
- HR assigned to the vacancy manages committees and interviews; staff who can
  see the vacancy (assigned HR, assigned Department Heads, approvers) can see
  submitted evaluations. Drafts are visible only to their author.
"""

from django.conf import settings

from accounts import roles
from recruitment import selectors as vacancy_selectors
from recruitment.models import Application, DecisionOutcome, DecisionType, RecruitmentDecision

from .models import (BLOCKING_CONFLICTS, CommitteeConflict, CommitteeMember, EvaluationStatus,
                     InterviewEvaluation)


def is_member_role(user):
    return roles.has_role(user, *settings.COMMITTEE_MEMBER_ROLES)


def active_memberships(user):
    if not is_member_role(user):
        return CommitteeMember.objects.none()
    return CommitteeMember.objects.filter(
        user=user, ended_at__isnull=True, committee__is_active=True
    ).select_related('committee__vacancy')


def membership_for(user, application):
    """The user's active membership on a committee for the application's vacancy."""
    return active_memberships(user).filter(
        committee__vacancy_id=application.vacancy_id).order_by('created_at').first()


def shortlisted_ids():
    return RecruitmentDecision.objects.filter(
        decision_type=DecisionType.SHORTLISTING, outcome=DecisionOutcome.SHORTLISTED,
        superseded_by__isnull=True).values('application_id')


def blocked_ids(user):
    return CommitteeConflict.objects.filter(
        committee_member__user=user, status__in=BLOCKING_CONFLICTS).values('application_id')


def listed_candidates(user):
    """Shortlisted applications of the user's committee vacancies (blocked ones included,
    so a member can see that their conflict is recorded)."""
    vacancies = active_memberships(user).values('committee__vacancy_id')
    return Application.objects.filter(vacancy_id__in=vacancies, pk__in=shortlisted_ids()) \
        .select_related('vacancy__department', 'applicant__user') \
        .order_by('vacancy__position_title', 'applicant__user__last_name')


def candidate_applications(user):
    """Applications a committee member may open and assess."""
    return listed_candidates(user).exclude(pk__in=blocked_ids(user))


def can_view_candidate(user, application):
    return candidate_applications(user).filter(pk=application.pk).exists()


def has_blocking_conflict(member, application):
    return member.conflicts.filter(application=application,
                                   status__in=BLOCKING_CONFLICTS).exists()


def can_manage(user, vacancy):
    """Create committees, add members, schedule interviews: assigned HR."""
    return vacancy_selectors.can_manage(user, vacancy)


def can_oversee(user, vacancy):
    """See committees, conflicts, interviews, and submitted evaluations."""
    return vacancy_selectors.can_view(user, vacancy)


def visible_evaluations(user, application):
    """Submitted evaluations (all versions) for staff who can see the vacancy;
    otherwise only the user's own evaluations, drafts included."""
    qs = InterviewEvaluation.objects.filter(application=application).select_related(
        'committee_member__user', 'interview', 'supersedes')
    if can_oversee(user, application.vacancy):
        own = qs.filter(committee_member__user=user)
        return (qs.filter(status=EvaluationStatus.SUBMITTED) | own).distinct()
    return qs.filter(committee_member__user=user)
