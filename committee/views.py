from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from accounts.access import role_required
from recruitment import application_services
from recruitment.application_views import _snapshot_view
from recruitment.models import Application, FactReviewStatus, FactType, Vacancy

from . import selectors, services
from .forms import (AmendEvaluationForm, AttendanceForm, CommitteeForm, ConflictForm,
                    EvaluationForm, InterviewForm, MemberForm, ReasonForm, RescheduleForm,
                    ResolveConflictForm)
from .models import (Committee, CommitteeConflict, CommitteeMember, ConflictStatus, Interview,
                     InterviewEvaluation, InterviewStatus)

STAFF = (roles.HR, roles.APPROVER, roles.DEPARTMENT_HEAD)
MEMBERS = (roles.PSB_MEMBER, roles.DEPARTMENT_HEAD)


def _flash(request, exc):
    for message in getattr(exc, 'messages', [str(exc)]):
        messages.error(request, message)


def _vacancy_or_404(user, pk):
    vacancy = get_object_or_404(Vacancy.objects.select_related('department'), pk=pk)
    if not selectors.can_oversee(user, vacancy):
        raise Http404
    return vacancy


def _require_manage(user, vacancy):
    if not selectors.can_manage(user, vacancy):
        raise PermissionDenied


def _app_url(application):
    return redirect('recruitment:application_selection', pk=application.pk)


# ==========================================================================
# Committees (HR manages; assigned staff and approvers view)
# ==========================================================================

@role_required(*STAFF)
@require_http_methods(['GET', 'POST'])
def vacancy_committees(request, pk):
    vacancy = _vacancy_or_404(request.user, pk)
    can_manage = selectors.can_manage(request.user, vacancy)
    form = CommitteeForm(request.POST or None)
    if request.method == 'POST':
        _require_manage(request.user, vacancy)
        if form.is_valid():
            try:
                services.create_committee(request.user, vacancy, name=form.cleaned_data['name'])
            except ValidationError as exc:
                _flash(request, exc)
            else:
                messages.success(request, 'Committee created. Add its members next.')
                return redirect('committee:vacancy_committees', pk=pk)
    committees = []
    for committee in vacancy.committees.prefetch_related('members__user'):
        committees.append({
            'committee': committee,
            'members': committee.members.select_related('user').order_by('ended_at',
                                                                         'member_role'),
            'member_form': MemberForm(users=services.eligible_member_users(committee),
                                      prefix=str(committee.pk)),
        })
    conflicts = CommitteeConflict.objects.filter(
        application__vacancy=vacancy).select_related(
        'committee_member__user', 'committee_member__committee', 'application__applicant__user',
        'resolved_by')
    interviews = Interview.objects.filter(application__vacancy=vacancy).select_related(
        'application__applicant__user').prefetch_related('panel__committee_member__user')
    return render(request, 'committee/vacancy_committees.html', {
        'vacancy': vacancy, 'can_manage': can_manage, 'form': form, 'committees': committees,
        'conflicts': conflicts, 'interviews': interviews,
        'can_resolve': services.can_resolve_conflicts(request.user, vacancy),
        'reason_form': ReasonForm(),
    })


@role_required(roles.HR)
@require_POST
def member_add(request, committee_pk):
    committee = get_object_or_404(Committee.objects.select_related('vacancy'), pk=committee_pk)
    _vacancy_or_404(request.user, committee.vacancy_id)
    _require_manage(request.user, committee.vacancy)
    form = MemberForm(request.POST, users=services.eligible_member_users(committee),
                      prefix=str(committee.pk))
    if form.is_valid():
        try:
            services.add_member(request.user, committee, form.cleaned_data['user'],
                                member_role=form.cleaned_data['member_role'])
        except ValidationError as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Member added.')
    else:
        messages.error(request, 'Choose a member and their role.')
    return redirect('committee:vacancy_committees', pk=committee.vacancy_id)


@role_required(roles.HR)
@require_POST
def member_end(request, pk):
    member = get_object_or_404(CommitteeMember.objects.select_related('committee__vacancy'),
                               pk=pk)
    _vacancy_or_404(request.user, member.committee.vacancy_id)
    _require_manage(request.user, member.committee.vacancy)
    try:
        services.end_member(request.user, member, reason=request.POST.get('reason', '').strip())
    except ValidationError as exc:
        _flash(request, exc)
    else:
        messages.success(request, 'Membership ended. Earlier evaluations are kept.')
    return redirect('committee:vacancy_committees', pk=member.committee.vacancy_id)


@role_required(roles.HR)
@require_POST
def committee_active(request, pk):
    committee = get_object_or_404(Committee.objects.select_related('vacancy'), pk=pk)
    _vacancy_or_404(request.user, committee.vacancy_id)
    _require_manage(request.user, committee.vacancy)
    try:
        services.set_committee_active(request.user, committee,
                                      active=request.POST.get('active') == '1',
                                      reason=request.POST.get('reason', '').strip())
    except ValidationError as exc:
        _flash(request, exc)
    return redirect('committee:vacancy_committees', pk=committee.vacancy_id)


@role_required(roles.APPROVER)
@require_http_methods(['GET', 'POST'])
def conflict_resolve(request, pk):
    conflict = get_object_or_404(CommitteeConflict.objects.select_related(
        'application__vacancy', 'application__applicant__user', 'committee_member__user'), pk=pk)
    vacancy = _vacancy_or_404(request.user, conflict.application.vacancy_id)
    form = ResolveConflictForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            services.resolve_conflict(request.user, conflict, **form.cleaned_data)
        except ValidationError as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Conflict resolved.')
            return redirect('committee:vacancy_committees', pk=vacancy.pk)
    return render(request, 'committee/conflict_resolve.html',
                  {'conflict': conflict, 'form': form, 'vacancy': vacancy})


# ==========================================================================
# Interviews (HR assigned to the vacancy)
# ==========================================================================

def _managed_application(request, pk):
    application = get_object_or_404(Application.objects.select_related(
        'vacancy', 'applicant__user'), pk=pk)
    _vacancy_or_404(request.user, application.vacancy_id)
    _require_manage(request.user, application.vacancy)
    return application


def _managed_interview(request, pk):
    interview = get_object_or_404(Interview.objects.select_related(
        'application__vacancy', 'application__applicant__user'), pk=pk)
    _managed_application(request, interview.application_id)
    return interview


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def interview_new(request, app_pk):
    application = _managed_application(request, app_pk)
    members = services.panel_candidates(application)
    form = InterviewForm(request.POST or None, members=members)
    problems = services.scheduling_problems(application)
    if request.method == 'POST' and form.is_valid():
        try:
            services.schedule_interview(request.user, application, **form.cleaned_data)
        except ValidationError as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Interview scheduled.')
            return _app_url(application)
    return render(request, 'committee/interview_form.html', {
        'application': application, 'form': form, 'problems': problems,
        'title': 'Schedule an interview', 'submit_label': 'Schedule interview'})


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def interview_reschedule(request, pk):
    interview = _managed_interview(request, pk)
    form = RescheduleForm(request.POST) if request.method == 'POST' \
        else RescheduleForm.for_interview(interview)
    if request.method == 'POST' and form.is_valid():
        try:
            services.reschedule_interview(request.user, interview, **form.cleaned_data)
        except ValidationError as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Interview rescheduled. The earlier schedule is kept in '
                                      'its history.')
            return _app_url(interview.application)
    return render(request, 'committee/interview_form.html', {
        'application': interview.application, 'interview': interview, 'form': form,
        'title': 'Reschedule interview', 'submit_label': 'Reschedule'})


@role_required(roles.HR)
@require_POST
def interview_cancel(request, pk):
    interview = _managed_interview(request, pk)
    try:
        services.cancel_interview(request.user, interview,
                                  reason=request.POST.get('reason', '').strip())
    except ValidationError as exc:
        _flash(request, exc)
    else:
        messages.success(request, 'Interview canceled.')
    return _app_url(interview.application)


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def interview_attendance(request, pk):
    interview = _managed_interview(request, pk)
    form = AttendanceForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            services.record_attendance(request.user, interview, **form.cleaned_data)
        except ValidationError as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Attendance recorded.')
            return _app_url(interview.application)
    return render(request, 'form_page.html', {
        'form': form, 'title': 'Record attendance',
        'intro': f'Interview on {interview.starts_at:%b %d, %Y %I:%M %p}. Recording attendance '
                 'completes the interview.',
        'submit_label': 'Save attendance', 'cancel_url': 'recruitment:application_selection',
        'cancel_arg': interview.application_id})


@role_required(roles.HR)
@require_POST
def panel_add(request, pk):
    interview = _managed_interview(request, pk)
    member = get_object_or_404(CommitteeMember, pk=request.POST.get('member'))
    try:
        services.add_panel_member(request.user, interview, member)
    except ValidationError as exc:
        _flash(request, exc)
    return _app_url(interview.application)


@role_required(roles.HR)
@require_POST
def panel_remove(request, pk, member_pk):
    interview = _managed_interview(request, pk)
    member = get_object_or_404(CommitteeMember, pk=member_pk)
    try:
        services.remove_panel_member(request.user, interview, member,
                                     reason=request.POST.get('reason', '').strip())
    except ValidationError as exc:
        _flash(request, exc)
    return _app_url(interview.application)


# ==========================================================================
# Committee members: candidates, conflicts, evaluations
# ==========================================================================

@role_required(*MEMBERS)
def my_candidates(request):
    blocked = set(selectors.blocked_ids(request.user).values_list('application_id', flat=True))
    rows = [(a, a.pk in blocked) for a in selectors.listed_candidates(request.user)]
    return render(request, 'committee/my_candidates.html', {
        'rows': rows, 'memberships': selectors.active_memberships(request.user)})


def _candidate_or_404(user, pk):
    try:
        return selectors.candidate_applications(user).get(pk=pk)
    except Application.DoesNotExist:
        raise Http404


@role_required(*MEMBERS)
def candidate(request, pk):
    application = _candidate_or_404(request.user, pk)
    member = selectors.membership_for(request.user, application)
    snapshot = application.latest_snapshot()
    facts = application.facts.filter(fact_type=FactType.QUALIFICATION_ASSESSMENT) \
        .exclude(review_status=FactReviewStatus.SUPERSEDED)
    interviews = application.interviews.prefetch_related('panel__committee_member__user',
                                                         'events')
    my_interviews = [i for i in interviews
                     if any(s.committee_member_id == member.pk for s in i.panel.all())]
    evaluations = selectors.visible_evaluations(request.user, application) \
        .filter(committee_member__user=request.user)
    started = {e.interview_id for e in evaluations}
    open_for = [i for i in my_interviews if i.status == InterviewStatus.COMPLETED
                and i.attendance_status == 'attended' and i.pk not in started]
    return render(request, 'committee/candidate.html', {
        'application': application, 'member': member, 'snapshot': snapshot,
        'snapshot_view': _snapshot_view(snapshot.data if snapshot else None),
        'outcomes': facts, 'interviews': my_interviews, 'evaluations': evaluations,
        'can_start': bool(open_for) or None not in started,
        'documents': application.documents.select_related('document_type'),
        'conflict_form': ConflictForm(),
    })


@role_required(*MEMBERS)
@require_POST
def conflict_declare(request, pk):
    application = _candidate_or_404(request.user, pk)
    member = selectors.membership_for(request.user, application)
    form = ConflictForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Describe the conflict of interest.')
        return redirect('committee:candidate', pk=pk)
    try:
        services.declare_conflict(request.user, member, application, **form.cleaned_data)
    except ValidationError as exc:
        _flash(request, exc)
        return redirect('committee:candidate', pk=pk)
    messages.success(request, 'Conflict declared. You will not take part in assessing this '
                              'applicant unless an authorized officer clears it.')
    return redirect('committee:my_candidates')


@role_required(*MEMBERS)
@require_http_methods(['GET', 'POST'])
def evaluation_new(request, pk):
    application = _candidate_or_404(request.user, pk)
    member = selectors.membership_for(request.user, application)
    started = set(InterviewEvaluation.objects.filter(
        application=application, committee_member=member).values_list('interview_id',
                                                                         flat=True))
    interviews = [i for i in application.interviews.filter(
        status=InterviewStatus.COMPLETED, attendance_status='attended',
        panel__committee_member=member) if i.pk not in started]
    form = EvaluationForm(request.POST or None, interviews=interviews)
    if request.method == 'POST' and form.is_valid():
        interview_pk = form.cleaned_data.pop('interview', '')
        interview = next((i for i in interviews if str(i.pk) == interview_pk), None)
        try:
            evaluation = services.start_evaluation(request.user, application,
                                                   interview=interview, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Draft saved. Submit it when it is final.')
            return redirect('committee:evaluation_edit', pk=evaluation.pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': 'New evaluation',
        'intro': f'{application.applicant.user.full_name} - {application.vacancy.position_title}'
                 '. Saved as a draft that only you can see until you submit it.',
        'submit_label': 'Save draft', 'cancel_url': 'committee:candidate',
        'cancel_arg': application.pk})


def _own_evaluation_or_404(user, pk):
    evaluation = get_object_or_404(InterviewEvaluation.objects.select_related(
        'application__vacancy', 'application__applicant__user', 'committee_member', 'interview',
        'supersedes'), pk=pk, committee_member__user=user)
    _candidate_or_404(user, evaluation.application_id)
    return evaluation


@role_required(*MEMBERS)
@require_http_methods(['GET', 'POST'])
def evaluation_edit(request, pk):
    evaluation = _own_evaluation_or_404(request.user, pk)
    form = EvaluationForm(request.POST or None, initial={
        'findings': evaluation.findings, 'recommendation': evaluation.recommendation})
    if request.method == 'POST' and evaluation.status == 'draft' and form.is_valid():
        try:
            services.update_draft(request.user, evaluation, **form.cleaned_data)
            if 'submit' in request.POST:
                services.submit_evaluation(request.user, evaluation)
        except (ValidationError, PermissionDenied) as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Evaluation submitted.' if 'submit' in request.POST
                             else 'Draft saved.')
            return redirect('committee:evaluation_edit', pk=pk)
    evaluation.refresh_from_db()
    history = InterviewEvaluation.objects.filter(
        application=evaluation.application, committee_member=evaluation.committee_member,
        interview=evaluation.interview).order_by('-version')
    return render(request, 'committee/evaluation.html', {
        'evaluation': evaluation, 'form': form, 'history': history,
        'can_amend': evaluation.is_submitted and not hasattr(evaluation, 'superseded_by')})


@role_required(*MEMBERS)
@require_POST
def evaluation_discard(request, pk):
    evaluation = _own_evaluation_or_404(request.user, pk)
    application_pk = evaluation.application_id
    try:
        services.discard_draft(request.user, evaluation)
    except (ValidationError, PermissionDenied) as exc:
        _flash(request, exc)
        return redirect('committee:evaluation_edit', pk=pk)
    messages.success(request, 'Draft discarded.')
    return redirect('committee:candidate', pk=application_pk)


@role_required(*MEMBERS)
@require_http_methods(['GET', 'POST'])
def evaluation_amend(request, pk):
    evaluation = _own_evaluation_or_404(request.user, pk)
    form = AmendEvaluationForm(request.POST or None, initial={
        'findings': evaluation.findings, 'recommendation': evaluation.recommendation})
    if request.method == 'POST' and form.is_valid():
        try:
            amendment = services.amend_evaluation(request.user, evaluation, **form.cleaned_data)
        except (ValidationError, PermissionDenied) as exc:
            _flash(request, exc)
        else:
            messages.success(request, f'Amendment saved as draft version {amendment.version}. '
                                      'Submit it to replace the earlier version.')
            return redirect('committee:evaluation_edit', pk=amendment.pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Amend evaluation (version {evaluation.version})',
        'intro': 'The submitted version stays on record unchanged. Your amendment becomes the '
                 'current version once you submit it.',
        'submit_label': 'Save amendment draft', 'cancel_url': 'committee:evaluation_edit',
        'cancel_arg': evaluation.pk})
