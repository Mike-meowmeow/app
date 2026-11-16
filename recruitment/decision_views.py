"""Selection page (interviews, evaluations, conflicts, decisions) and
decision recording/release screens for staff (FR-010, FR-011, FR-019)."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts import roles
from accounts.access import role_required

from . import decision_services as decisions
from .application_forms import CorrectDecisionForm, DecisionForm, ReleaseForm
from .application_views import STAFF_ROLES, _flash, _visible_or_404
from .models import DecisionType, RecruitmentDecision


@role_required(*STAFF_ROLES)
def application_selection(request, pk):
    from committee import selectors as committee_selectors
    from committee import services as committee_services
    from committee.forms import ReasonForm

    application = _visible_or_404(request.user, pk)
    can_manage = committee_selectors.can_manage(request.user, application.vacancy)
    records = list(application.decisions.select_related(
        'decided_by', 'released_by', 'committee', 'superseded_by'))
    interviews = list(application.interviews.select_related('scheduled_by').prefetch_related(
        'panel__committee_member__user', 'events__actor'))
    panel_choices = committee_services.panel_candidates(application) if can_manage else []
    for interview in interviews:
        interview.conflicted = {s.committee_member_id
                                for s in committee_services.panel_conflicts(interview)}
        seated = {s.committee_member_id for s in interview.panel.all()}
        interview.addable = [m for m in panel_choices if m.pk not in seated]
    return render(request, 'recruitment/applications/selection.html', {
        'application': application, 'can_manage': can_manage,
        'decisions': [(d, d.is_current, d.is_current and not d.is_released
                       and decisions.can_release(request.user, d),
                       d.is_current and decisions.can_record(request.user, application,
                                                             d.decision_type))
                      for d in records],
        'recordable': decisions.recordable_types(request.user, application),
        'interviews': interviews,
        'scheduling_problems': committee_services.scheduling_problems(application),
        'evaluations': committee_selectors.visible_evaluations(request.user, application)
                       .order_by('committee_member__user__last_name', 'interview', 'version'),
        'conflicts': application.committee_conflicts.select_related(
            'committee_member__user', 'resolved_by'),
        'can_resolve': committee_services.can_resolve_conflicts(request.user,
                                                                application.vacancy),
        'reason_form': ReasonForm(),
        'applicant_status': decisions.PublicStatus(decisions.applicant_status(application)),
    })


@role_required(roles.HR, roles.APPROVER)
@require_http_methods(['GET', 'POST'])
def decision_new(request, pk, decision_type):
    application = _visible_or_404(request.user, pk)
    if decision_type not in DecisionType.values:
        raise Http404
    if not decisions.can_record(request.user, application, decision_type):
        raise PermissionDenied
    outcomes = dict(decisions.recordable_types(request.user, application)).get(decision_type)
    if not outcomes:
        messages.error(request, 'This decision cannot be recorded at the current stage.')
        return redirect('recruitment:application_selection', pk=pk)
    form = DecisionForm(request.POST or None, decision_type=decision_type, outcomes=outcomes,
                        committees=application.vacancy.committees.filter(is_active=True))
    if request.method == 'POST' and form.is_valid():
        try:
            decisions.record_decision(request.user, application, decision_type=decision_type,
                                      **form.cleaned_data)
        except ValidationError as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Decision recorded. It is internal until released.')
            return redirect('recruitment:application_selection', pk=pk)
    label = DecisionType(decision_type).label
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Record {label.lower()}',
        'intro': f'{application.applicant.user.full_name} ({application.reference}). The '
                 'decision is recorded internally; the applicant sees it only after release.',
        'submit_label': 'Record decision', 'cancel_url': 'recruitment:application_selection',
        'cancel_arg': application.pk})


def _decision_or_404(request, pk, decision_pk):
    application = _visible_or_404(request.user, pk)
    return get_object_or_404(RecruitmentDecision.objects.select_related('application__vacancy'),
                             pk=decision_pk, application=application)


@role_required(roles.HR, roles.APPROVER)
@require_http_methods(['GET', 'POST'])
def decision_correct(request, pk, decision_pk):
    decision = _decision_or_404(request, pk, decision_pk)
    if not decisions.can_record(request.user, decision.application, decision.decision_type):
        raise PermissionDenied
    form = CorrectDecisionForm(request.POST or None, decision=decision)
    if request.method == 'POST' and form.is_valid():
        try:
            decisions.correct_decision(request.user, decision, **form.cleaned_data)
        except ValidationError as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Correction recorded; the earlier record is kept.')
            return redirect('recruitment:application_selection', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Correct decision record: {decision}',
        'intro': 'The outcome stays the same. To change an outcome, reopen the application and '
                 'record a new decision.',
        'submit_label': 'Record correction', 'cancel_url': 'recruitment:application_selection',
        'cancel_arg': pk})


@role_required(roles.HR, roles.APPROVER)
@require_http_methods(['GET', 'POST'])
def decision_release(request, pk, decision_pk):
    decision = _decision_or_404(request, pk, decision_pk)
    if not decisions.can_release(request.user, decision):
        raise PermissionDenied
    form = ReleaseForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            decisions.release_decision(request.user, decision, **form.cleaned_data)
        except ValidationError as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Decision released to the applicant.')
            nxt = request.POST.get('next')
            if nxt == 'pending':
                return redirect('recruitment:decisions_pending')
            return redirect('recruitment:application_selection', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Release to applicant: {decision}',
        'intro': f'{decision.application.applicant.user.full_name} '
                 f'({decision.application.reference}) will see this decision and your message.',
        'submit_label': 'Release', 'cancel_url': 'recruitment:application_selection',
        'cancel_arg': pk})


@role_required(roles.HR, roles.APPROVER)
def decisions_pending(request):
    return render(request, 'recruitment/applications/decisions_pending.html', {
        'decisions': decisions.pending_release(request.user)})
