"""Business events -> recipients and generic messages (FR-014).

Each function is called by the owning service inside its transaction, after
the business change has been made. Messages never contain internal notes,
evaluation text, document contents, or correction details.
"""

from django.conf import settings
from django.utils import timezone

from accounts import roles

from .models import Category
from .services import notify, notify_many


def _vacancy_hr(vacancy):
    from recruitment.models import AssignmentRole
    return [a.user for a in vacancy.assignments.filter(
        assignment_role=AssignmentRole.HR, ended_at__isnull=True).select_related('user')
        if roles.has_role(a.user, roles.HR)]


def _users_with_role(*role_names):
    from accounts.models import User
    return User.objects.filter(is_active=True, groups__name__in=role_names).distinct()


def _applicant(application):
    return application.applicant.user


def _when(dt):
    return timezone.localtime(dt).strftime('%B %d, %Y %I:%M %p').replace(' 0', ' ')


def _position(application):
    return application.vacancy.position_title


# --------------------------------------------------------------------------
# Applications and corrections
# --------------------------------------------------------------------------

def application_submitted(application):
    notify(_applicant(application), category=Category.APPLICATION,
           event_key=f'application.submitted:{application.pk}',
           title='Application submitted',
           message=f'Your application {application.reference} for {_position(application)} was '
                   f'received on {_when(application.submitted_at)}.',
           application=application)
    notify_many(_vacancy_hr(application.vacancy), category=Category.APPLICATION,
                event_key=f'application.submitted:{application.pk}',
                title='New application to review',
                message=f'Application {application.reference} for {_position(application)} was '
                        'submitted and is waiting for review.',
                application=application)


def correction_requested(request):
    application = request.application
    notify(_applicant(application), category=Category.VERIFICATION,
           event_key=f'correction.requested:{request.pk}',
           title='Corrections requested',
           message=f'HR requested corrections to your application {application.reference} for '
                   f'{_position(application)}. Please submit them before '
                   f'{_when(request.deadline_at)} (Asia/Manila).',
           application=application)


def corrections_submitted(request):
    application = request.application
    notify_many(_vacancy_hr(application.vacancy), category=Category.VERIFICATION,
                event_key=f'correction.submitted:{request.pk}',
                title='Corrections submitted',
                message=f'The applicant submitted corrections for {application.reference} '
                        f'({_position(application)}).',
                application=application)


def application_withdrawn(application):
    notify_many(_vacancy_hr(application.vacancy), category=Category.APPLICATION,
                event_key=f'application.withdrawn:{application.pk}:{application.revision}',
                title='Application withdrawn',
                message=f'Application {application.reference} for {_position(application)} was '
                        'withdrawn by the applicant.',
                application=application)


# --------------------------------------------------------------------------
# Decisions (only after authorized release)
# --------------------------------------------------------------------------

def decision_released(decision):
    application = decision.application
    notify(_applicant(application), category=Category.RECOMMENDATION,
           event_key=f'decision.released:{decision.pk}',
           title=f'Update on your application: {decision.get_decision_type_display()}',
           message=f'There is an update on your application {application.reference} for '
                   f'{_position(application)}. Open your application to read it.',
           application=application)


# --------------------------------------------------------------------------
# Committees and interviews
# --------------------------------------------------------------------------

def committee_member_added(member):
    stamp = int(member.updated_at.timestamp())
    notify(member.user, category=Category.INTERVIEW,
           event_key=f'committee.member:{member.pk}:{stamp}',
           title='Committee assignment',
           message=f'You were added to {member.committee.name} '
                   f'({member.committee.vacancy.position_title}) as '
                   f'{member.get_member_role_display().lower()}. Review the assigned candidates '
                   'and declare any conflict of interest before assessing them.')


def conflict_declared(conflict):
    vacancy = conflict.application.vacancy
    from recruitment import selectors
    resolvers = [u for u in _users_with_role(*settings.CONFLICT_RESOLUTION_ROLES)
                 if selectors.can_view(u, vacancy) and u.pk != conflict.committee_member.user_id]
    notify_many(resolvers, category=Category.INTERVIEW,
                event_key=f'conflict.declared:{conflict.pk}',
                title='Conflict of interest to resolve',
                message=f'A committee member declared a conflict of interest for an applicant to '
                        f'{vacancy.position_title}. It needs an authorized resolution.',
                application=conflict.application)


def _interview_recipients(interview):
    return [seat.committee_member.user for seat in
            interview.panel.select_related('committee_member__user')]


def interview_event(interview, event):
    """Scheduled, rescheduled, or canceled: tell the applicant and the panel."""
    application = interview.application
    kind = event.event_type
    if kind == 'scheduled':
        title = 'Interview invitation'
        detail = f'is scheduled on {_when(interview.starts_at)} ({interview.timezone})'
    elif kind == 'rescheduled':
        title = 'Interview rescheduled'
        detail = f'was moved to {_when(interview.starts_at)} ({interview.timezone})'
    elif kind == 'canceled':
        title = 'Interview canceled'
        detail = 'was canceled'
    else:
        return
    key = f'interview.{kind}:{event.pk}'
    notify(_applicant(application), category=Category.INTERVIEW, event_key=key, title=title,
           message=f'Your interview for {_position(application)} {detail}. Open your '
                   'application for the venue or meeting link.',
           application=application, interview=interview)
    notify_many(_interview_recipients(interview), category=Category.INTERVIEW, event_key=key,
                title=title,
                message=f'A panel interview for {_position(application)} {detail}.',
                application=application, interview=interview)


def panel_member_added(interview, member, event):
    notify(member.user, category=Category.INTERVIEW,
           event_key=f'interview.panel_added:{event.pk}',
           title='Added to an interview panel',
           message=f'You were added to an interview panel for {_position(interview.application)} '
                   f'on {_when(interview.starts_at)} ({interview.timezone}).',
           application=interview.application, interview=interview)


# --------------------------------------------------------------------------
# Official roster approval
# --------------------------------------------------------------------------

def roster_event(roster, event):
    vacancy = roster.vacancy
    label = f'roster version {roster.version} for {vacancy.position_title}'
    key = f'roster.{event.to_status}:{event.pk}'
    if event.to_status == 'pending_approval':
        approvers = [u for u in _users_with_role(roles.APPROVER) if u.pk != roster.prepared_by_id
                     or settings.ROSTER_ALLOW_SELF_APPROVAL]
        notify_many(approvers, category=Category.ROSTER_APPROVAL, event_key=key,
                    title='Roster awaiting approval',
                    message=f'The official {label} was sent for approval.', roster=roster)
    elif event.to_status in ('returned', 'approved'):
        word = 'returned for correction' if event.to_status == 'returned' else 'approved'
        notify(roster.prepared_by, category=Category.ROSTER_APPROVAL, event_key=key,
               title=f'Roster {word}', message=f'The official {label} was {word}.',
               roster=roster)
