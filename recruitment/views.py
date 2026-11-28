from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from accounts.access import role_required

from . import decision_services as decisions
from . import merit, position_services, selectors, services
from .forms import (ApplyPositionForm, AssignmentForm, CompetencyForm, DocumentRequirementForm,
                    QualificationStandardForm, ReasonForm, VacancyFilterForm, VacancyForm)
from .models import (Application, ApplicationStatus, Position, Vacancy, VacancyAssignment,
                     VacancyStatus)

STAFF_ROLES = (roles.HR, roles.APPROVER, roles.DEPARTMENT_HEAD)


def _visible_or_404(user, pk):
    """Vacancies outside the user's assignments look like they don't exist."""
    try:
        return selectors.visible_vacancies(user).get(pk=pk)
    except Vacancy.DoesNotExist:
        raise Http404


def _flash_errors(request, exc):
    for message in getattr(exc, 'messages', [str(exc)]):
        messages.error(request, message)


# --------------------------------------------------------------------------
# Staff: list, create, view, edit
# --------------------------------------------------------------------------

@role_required(*STAFF_ROLES)
def vacancy_list(request):
    form = VacancyFilterForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    vacancies = selectors.search(selectors.visible_vacancies(request.user),
                                 q=filters.get('q', ''),
                                 department=filters.get('department'),
                                 recruitment_type=filters.get('recruitment_type'),
                                 status=filters.get('status', ''))
    page = Paginator(vacancies, 20).get_page(request.GET.get('page'))
    counts = submitted_counts(page)
    for vacancy in page:
        vacancy.merit_warning = merit.publication_warning(vacancy)
        vacancy.submitted_count = counts.get(vacancy.pk, 0)
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'recruitment/vacancy_list.html', {
        'form': form, 'page': page, 'query': query.urlencode(),
        'can_create': roles.has_role(request.user, roles.HR)})


def submitted_counts(vacancies):
    """{vacancy id: submitted applications}, counted like the vacancy page's
    "Applications (N)" button: every submitted application, including ones
    later withdrawn. Drafts are private to the applicant and never counted."""
    return dict(Application.objects.filter(
        vacancy__in=[v.pk for v in vacancies], submitted_at__isnull=False)
        .values('vacancy').annotate(n=Count('pk')).values_list('vacancy', 'n'))


def applicant_badges(user, vacancies):
    """{vacancy id: 'applied' | 'draft' | 'withdrawn'} for the applicant's own
    applications among these vacancies."""
    if not roles.has_role(user, roles.APPLICANT):
        return {}
    badges = {}
    for vacancy_id, status, submitted_at in Application.objects.filter(
            applicant__user=user, vacancy__in=[v.pk for v in vacancies]).values_list(
            'vacancy', 'status', 'submitted_at'):
        if status == ApplicationStatus.WITHDRAWN:
            badges[vacancy_id] = 'withdrawn'
        elif submitted_at:
            badges[vacancy_id] = 'applied'
        else:
            badges[vacancy_id] = 'draft'
    return badges


def _position_defaults():
    """Catalog defaults for the vacancy form's pre-fill and preview."""
    return {str(p.pk): position_services.defaults_manifest(p) for p in
            Position.objects.filter(is_active=True).prefetch_related(
                'document_requirements__document_type', 'qualification_standards',
                'competencies')}


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def vacancy_create(request):
    form = VacancyForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = {k: v for k, v in form.cleaned_data.items() if k != 'reason'}
        try:
            vacancy = services.create_vacancy(request.user, **data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Draft vacancy created with the position's default "
                                      'requirements. Review and adjust them, then publish.')
            return redirect('recruitment:vacancy_detail', pk=vacancy.pk)
    return render(request, 'recruitment/vacancy_create.html', {
        'form': form, 'position_defaults': _position_defaults(),
        'has_positions': Position.objects.filter(is_active=True).exists()})


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def vacancy_apply_position(request, pk):
    """Explicitly load a position's defaults into a never-published draft."""
    vacancy = _visible_or_404(request.user, pk)
    if not selectors.can_manage(request.user, vacancy):
        raise PermissionDenied
    initial = {'position': vacancy.position_id} if vacancy.position_id else None
    form = ApplyPositionForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        try:
            services.apply_position_defaults(request.user, vacancy, form.cleaned_data['position'],
                                             confirm=form.cleaned_data['confirm'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Position defaults loaded into the draft.')
            return redirect('recruitment:vacancy_detail', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': 'Load defaults from a position',
        'intro': f'{vacancy.position_title} · draft. This replaces the title, description and '
                 'every draft requirement (including your edits) with the chosen position\'s '
                 'current defaults. The replaced values are kept in the audit trail.',
        'submit_label': 'Replace with position defaults',
        'cancel_url': 'recruitment:vacancy_detail', 'cancel_arg': pk})


@role_required(*STAFF_ROLES)
def vacancy_detail(request, pk):
    vacancy = _visible_or_404(request.user, pk)
    can_manage = selectors.can_manage(request.user, vacancy)
    draft = vacancy.draft_requirements()
    current = vacancy.current_requirements()
    context = {
        'vacancy': vacancy, 'draft': draft, 'current': current,
        'versions': vacancy.requirement_versions.filter(published_at__isnull=False)
                    .select_related('published_by'),
        'can_manage': can_manage,
        'can_view_roster': can_manage or roles.has_role(request.user, roles.APPROVER),
        'can_assign': selectors.can_assign_staff(request.user, vacancy),
        'assignments': vacancy.assignments.select_related('user', 'assigned_by'),
        'problems': services.publication_problems(vacancy, draft) if draft else [],
        'merit_warning': merit.publication_warning(vacancy),
        'editable': vacancy.status in (VacancyStatus.DRAFT, VacancyStatus.PUBLISHED),
        'reason_form': ReasonForm(),
        'assignment_form': AssignmentForm(),
        'submitted_count': vacancy.applications.filter(submitted_at__isnull=False).count(),
        'can_apply_position': can_manage and vacancy.status == VacancyStatus.DRAFT
                              and not vacancy.posted_at and current is None,
    }
    return render(request, 'recruitment/vacancy_detail.html', context)


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def vacancy_edit(request, pk):
    vacancy = _visible_or_404(request.user, pk)
    if not selectors.can_manage(request.user, vacancy):
        raise PermissionDenied
    form = VacancyForm(request.POST or None, instance=vacancy)
    if request.method == 'POST' and form.is_valid():
        data = {k: v for k, v in form.cleaned_data.items()
                if k != 'reason' and k in form.changed_data}
        try:
            services.update_vacancy(request.user, Vacancy.objects.get(pk=pk),
                                    reason=form.cleaned_data.get('reason', ''), **data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Vacancy updated.')
            return redirect('recruitment:vacancy_detail', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Edit {vacancy.position_title}', 'submit_label': 'Save',
        'cancel_url': 'recruitment:vacancy_detail', 'cancel_arg': pk})


@role_required(*STAFF_ROLES)
def version_detail(request, pk, version):
    vacancy = _visible_or_404(request.user, pk)
    req = get_object_or_404(vacancy.requirement_versions, version=version,
                            published_at__isnull=False)
    return render(request, 'recruitment/version_detail.html', {'vacancy': vacancy, 'req': req})


# --------------------------------------------------------------------------
# Staff: requirements
# --------------------------------------------------------------------------

ITEM_FORMS = {
    'document': ('Add required document', DocumentRequirementForm),
    'standard': ('Add qualification standard', QualificationStandardForm),
    'competency': ('Add subject or competency', CompetencyForm),
}


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def requirement_add(request, pk, kind):
    vacancy = _visible_or_404(request.user, pk)
    if kind not in ITEM_FORMS or not selectors.can_manage(request.user, vacancy):
        raise PermissionDenied
    draft = vacancy.draft_requirements()
    if draft is None:
        messages.error(request, 'Start a requirements update first.')
        return redirect('recruitment:vacancy_detail', pk=pk)
    title, form_class = ITEM_FORMS[kind]
    kwargs = {'draft': draft} if kind == 'document' else {}
    form = form_class(request.POST or None, **kwargs)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            if kind == 'document':
                services.add_document_requirement(request.user, vacancy, **data)
            elif kind == 'standard':
                services.add_qualification_standard(
                    request.user, vacancy, criterion_type=data['criterion_type'],
                    description=data['description'], degree_level=data['degree_level'],
                    amount=data['amount'], name=data['name'], field=data['field'])
            else:
                services.add_competency(request.user, vacancy, **data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Requirement added to the draft.')
            return redirect('recruitment:vacancy_detail', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': title, 'intro': f'{vacancy.position_title} · draft version '
                                              f'{draft.version}',
        'submit_label': 'Add', 'cancel_url': 'recruitment:vacancy_detail', 'cancel_arg': pk})


def _action(request, pk, func, success, *, needs_reason=False, **extra):
    vacancy = _visible_or_404(request.user, pk)
    reason = request.POST.get('reason', '').strip()
    if needs_reason and not reason:
        messages.error(request, 'Please give a reason.')
        return redirect('recruitment:vacancy_detail', pk=pk)
    try:
        kwargs = dict(extra)
        if needs_reason or 'reason' in request.POST:
            kwargs['reason'] = reason
        func(request.user, vacancy, **kwargs)
    except PermissionDenied:
        raise
    except ValidationError as exc:
        _flash_errors(request, exc)
    else:
        messages.success(request, success)
    return redirect('recruitment:vacancy_detail', pk=pk)


@role_required(roles.HR)
@require_POST
def requirement_keep(request, pk, item_pk):
    """Keep a "board-exam courses only" standard: it applies to this vacancy."""
    return _action(request, pk, lambda u, v: services.keep_board_standard(u, v, item_pk),
                   'Standard kept: it applies to this vacancy.')


@role_required(roles.HR)
@require_POST
def requirement_remove(request, pk, kind, item_pk):
    if kind not in services.ITEM_MODELS:
        raise Http404
    return _action(request, pk, lambda u, v: services.remove_requirement_item(u, v, kind, item_pk),
                   'Requirement removed from the draft.')


@role_required(roles.HR)
@require_POST
def requirements_start(request, pk):
    return _action(request, pk, services.start_requirements_update,
                   'Draft requirements created from the current version.')


@role_required(roles.HR)
@require_POST
def requirements_discard(request, pk):
    return _action(request, pk, services.discard_requirements_draft, 'Draft discarded.')


@role_required(roles.HR)
@require_POST
def vacancy_publish(request, pk):
    first_time = _visible_or_404(request.user, pk).status == VacancyStatus.DRAFT
    message = ('Vacancy published and its committee created. Assign the members under '
               'Committees & interviews.') if first_time else 'Published.'
    return _action(request, pk, services.publish, message)


@role_required(roles.HR)
@require_POST
def vacancy_close(request, pk):
    return _action(request, pk, services.close_vacancy, 'Vacancy closed.', needs_reason=True)


@role_required(roles.HR)
@require_POST
def vacancy_cancel(request, pk):
    return _action(request, pk, services.cancel_vacancy, 'Vacancy canceled.',
                   needs_reason=True)


@role_required(roles.HR)
@require_POST
def vacancy_delete(request, pk):
    vacancy = _visible_or_404(request.user, pk)
    try:
        services.delete_draft_vacancy(request.user, vacancy)
    except ValidationError as exc:
        _flash_errors(request, exc)
        return redirect('recruitment:vacancy_detail', pk=pk)
    messages.success(request, 'Draft vacancy deleted.')
    return redirect('recruitment:vacancy_list')


# --------------------------------------------------------------------------
# Staff: assignments
# --------------------------------------------------------------------------

@role_required(roles.HR, roles.APPROVER)
@require_POST
def assignment_add(request, pk):
    vacancy = _visible_or_404(request.user, pk)
    form = AssignmentForm(request.POST)
    if form.is_valid():
        try:
            services.assign_staff(request.user, vacancy, form.cleaned_data['user'],
                                  form.cleaned_data['assignment_role'],
                                  reason=request.POST.get('reason', ''))
        except ValidationError as exc:
            _flash_errors(request, exc)
        else:
            messages.success(request, 'Staff member assigned.')
    else:
        messages.error(request, 'Choose a staff member and role.')
    return redirect('recruitment:vacancy_detail', pk=pk)


@role_required(roles.HR, roles.APPROVER)
@require_POST
def assignment_end(request, pk, assignment_pk):
    vacancy = _visible_or_404(request.user, pk)
    assignment = get_object_or_404(VacancyAssignment, pk=assignment_pk, vacancy=vacancy)
    try:
        services.end_assignment(request.user, assignment, reason=request.POST.get('reason', ''))
    except ValidationError as exc:
        _flash_errors(request, exc)
    else:
        messages.success(request, 'Assignment ended.')
    return redirect('recruitment:vacancy_detail', pk=pk)


# --------------------------------------------------------------------------
# Public / applicants
# --------------------------------------------------------------------------

def public_list(request):
    vacancies = selectors.public_vacancies()
    q = request.GET.get('q', '').strip()
    if q:
        vacancies = selectors.search(vacancies, q=q)
    page = Paginator(vacancies, 20).get_page(request.GET.get('page'))
    badges = applicant_badges(request.user, page)
    for vacancy in page:
        vacancy.my_application = badges.get(vacancy.pk)
    return render(request, 'recruitment/public_list.html', {'page': page, 'q': q})


def public_detail(request, pk):
    vacancy = get_object_or_404(Vacancy.objects.select_related('department', 'recruitment_type'),
                                pk=pk, status=VacancyStatus.PUBLISHED)
    existing = public_status = None
    if roles.has_role(request.user, roles.APPLICANT):
        existing = vacancy.applications.filter(applicant__user=request.user).first()
        if existing:
            # The released status, as on My applications: internal decisions
            # stay hidden until HR or the approver releases them.
            public_status = decisions.PublicStatus(decisions.applicant_status(existing))
    return render(request, 'recruitment/public_detail.html', {
        'vacancy': vacancy, 'req': vacancy.current_requirements(), 'existing': existing,
        'public_status': public_status,
        'is_applicant': roles.has_role(request.user, roles.APPLICANT)})
