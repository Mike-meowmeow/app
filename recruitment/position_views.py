"""HR screens for the position catalog."""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from accounts.access import role_required

from . import position_services as catalog
from .forms import CompetencyForm, PositionDocumentForm, PositionForm, QualificationStandardForm
from .models import Position


def _flash_errors(request, exc):
    for message in getattr(exc, 'messages', [str(exc)]):
        messages.error(request, message)


@role_required(roles.HR)
def position_list(request):
    show = request.GET.get('show', 'active')
    positions = Position.objects.annotate(vacancy_count=Count('vacancies'))
    if show == 'active':
        positions = positions.filter(is_active=True)
    elif show == 'inactive':
        positions = positions.filter(is_active=False)
    q = request.GET.get('q', '').strip()
    if q:
        positions = positions.filter(title__icontains=q)
    page = Paginator(positions, 25).get_page(request.GET.get('page'))
    return render(request, 'recruitment/positions/list.html',
                  {'page': page, 'show': show, 'q': q})


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def position_create(request):
    form = PositionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            position = catalog.create_position(request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Position created. Add its default requirements.')
            return redirect('recruitment:position_detail', pk=position.pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': 'New position', 'submit_label': 'Create position',
        'intro': 'Defaults are copied into new vacancies; each vacancy can then be adjusted.',
        'cancel_url': 'recruitment:position_list'})


@role_required(roles.HR)
def position_detail(request, pk):
    position = get_object_or_404(Position, pk=pk)
    return render(request, 'recruitment/positions/detail.html', {
        'position': position,
        'vacancy_count': position.vacancies.count(),
        'documents': position.document_requirements.select_related('document_type'),
        'standards': position.qualification_standards.all(),
        'competencies': position.competencies.all(),
    })


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def position_edit(request, pk):
    position = get_object_or_404(Position, pk=pk)
    form = PositionForm(request.POST or None, instance=Position.objects.get(pk=pk))
    if request.method == 'POST' and form.is_valid():
        data = {k: form.cleaned_data[k] for k in ('title', 'description')
                if k in form.changed_data}
        try:
            catalog.update_position(request.user, position,
                                    reason=form.cleaned_data.get('reason', ''), **data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Position updated. Existing vacancies keep their own '
                                      'copies.')
            return redirect('recruitment:position_detail', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Edit {position.title}', 'submit_label': 'Save',
        'intro': 'Changes apply to vacancies created from now on. Existing vacancies, '
                 'including drafts, keep the description and requirements they copied.',
        'cancel_url': 'recruitment:position_detail', 'cancel_arg': pk})


@role_required(roles.HR)
@require_POST
def position_set_active(request, pk, action):
    position = get_object_or_404(Position, pk=pk)
    if action not in ('deactivate', 'reactivate'):
        raise Http404
    catalog.set_position_active(request.user, position, action == 'reactivate',
                                reason=request.POST.get('reason', '').strip())
    messages.success(request, 'Position reactivated.' if action == 'reactivate' else
                     'Position deactivated. It can no longer be chosen for new vacancies; '
                     'existing vacancies are unchanged.')
    return redirect('recruitment:position_detail', pk=pk)


ITEM_FORMS = {
    'document': ('Add default document', PositionDocumentForm),
    'standard': ('Add default qualification standard', QualificationStandardForm),
    'competency': ('Add default subject or competency', CompetencyForm),
}


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def position_item_add(request, pk, kind):
    position = get_object_or_404(Position, pk=pk)
    if kind not in ITEM_FORMS:
        raise Http404
    title, form_class = ITEM_FORMS[kind]
    kwargs = {'position': position} if kind == 'document' else {}
    form = form_class(request.POST or None, **kwargs)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            if kind == 'document':
                catalog.add_document_default(request.user, position, **data)
            elif kind == 'standard':
                catalog.add_standard_default(
                    request.user, position, criterion_type=data['criterion_type'],
                    description=data['description'], degree_level=data['degree_level'],
                    amount=data['amount'], name=data['name'], field=data['field'])
            else:
                catalog.add_competency_default(request.user, position, **data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Default requirement added.')
            return redirect('recruitment:position_detail', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': title, 'intro': f'{position.title} · catalog defaults',
        'submit_label': 'Add', 'cancel_url': 'recruitment:position_detail', 'cancel_arg': pk})


@role_required(roles.HR)
@require_POST
def position_item_remove(request, pk, kind, item_pk):
    position = get_object_or_404(Position, pk=pk)
    if kind not in catalog.ITEM_MODELS:
        raise Http404
    try:
        catalog.remove_default(request.user, position, kind, item_pk)
    except ValidationError as exc:
        _flash_errors(request, exc)
    else:
        messages.success(request, 'Default requirement removed.')
    return redirect('recruitment:position_detail', pk=pk)
