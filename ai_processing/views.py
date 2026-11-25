"""HR pages for AI extraction (13.3): run or retry a job, read the extracted
text beside the proposed items, and accept, correct, link, or reject each item.
Everything is advisory and labeled as such."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from accounts.access import role_required
from documents.models import DocumentVersion
from recruitment import application_selectors as access
from recruitment import fact_schema
from recruitment.application_forms import CorrectionReasonForm
from recruitment.models import Application, FactType

from . import services
from .models import AIJob, ExtractionItem, ItemReviewStatus

STAFF_ROLES = (roles.HR, roles.APPROVER, roles.DEPARTMENT_HEAD)


def _flash(request, exc):
    for message in getattr(exc, 'messages', [str(exc)]):
        messages.error(request, message)


def _visible(user, pk):
    try:
        return access.visible_applications(user).get(pk=pk)
    except Application.DoesNotExist:
        raise Http404


def _reviewable(request, pk):
    application = _visible(request.user, pk)
    if not access.can_review(request.user, application):
        raise PermissionDenied
    return application


def _job_url(pk, job_pk, anchor=''):
    return reverse('ai_processing:extraction', args=[pk, job_pk]) + anchor


def _numbered_pages(extraction, items):
    """Pages as [(number, method, confidence, [(line_no, text, highlighted)])]."""
    marked = {}
    for item in items:
        lines = (item.source_location or {}).get('lines') or []
        if item.page_number and len(lines) == 2:
            marked.setdefault(item.page_number, set()).update(range(lines[0], lines[1] + 1))
    pages = []
    for page in extraction.pages if extraction else []:
        if page.get('page') is None:
            continue
        rows = [line for line in (page.get('text') or '').splitlines() if line.strip()]
        highlight = marked.get(page['page'], set())
        pages.append({'number': page['page'], 'method': page.get('method'),
                      'confidence': page.get('ocr_confidence'),
                      'lines': [(i, ' '.join(text.split()), i in highlight)
                                for i, text in enumerate(rows, start=1)]})
    return pages


@role_required(*STAFF_ROLES)
def extraction(request, pk, job_pk):
    application = _visible(request.user, pk)
    job = get_object_or_404(AIJob.objects.select_related(
        'document_version__document__document_type', 'requested_by'), pk=job_pk,
        application=application)
    extraction = getattr(job, 'extraction', None)
    items = list(extraction.items.select_related('reviewed_by', 'resulting_fact')
                 .order_by('page_number', 'created_at')) if extraction else []
    facts = list(application.facts.exclude(fact_type__in=[FactType.IDENTITY,
                                                           FactType.QUALIFICATION_ASSESSMENT])
                 .order_by('fact_type', 'created_at'))
    current_facts = [f for f in facts if f.is_current]
    rows = []
    for item in items:
        same_type = [f for f in current_facts if f.fact_type == item.item_type]
        rows.append({
            'item': item,
            'label': FactType(item.item_type).label if item.item_type in FactType.values
            else item.item_type,
            'values': fact_schema.display_items(item.item_type, item.proposed_value)
            if item.item_type in fact_schema.FACT_FIELDS else [],
            'unread': [label for name, label, *_ in fact_schema.FACT_FIELDS.get(item.item_type, [])
                       if name in (item.source_location or {}).get('unread_fields', [])],
            'matches': services.matching_facts(item, current_facts),
            'link_choices': [(f, fact_schema.display_items(f.fact_type, f.value))
                             for f in same_type],
            'pending': item.review_status == ItemReviewStatus.PENDING,
        })
    return render(request, 'ai_processing/extraction.html', {
        'application': application, 'job': job, 'extraction': extraction, 'rows': rows,
        'pages': _numbered_pages(extraction, items),
        'attempts': job.attempts.order_by('attempt_number'),
        'can_review': access.can_review(request.user, application) and not job.is_invalidated,
        'version': job.document_version,
    })


@role_required(roles.HR)
@require_POST
def run(request, pk, version_pk):
    application = _reviewable(request, pk)
    version = get_object_or_404(DocumentVersion, pk=version_pk, document__application=application)
    try:
        job, created = services.queue_extraction(request.user, version)
    except ValidationError as exc:
        _flash(request, exc)
    else:
        messages.success(request, f'{version.file_name}: extraction queued.' if created
                         else f'{version.file_name}: extraction was already queued or done.')
    return redirect(reverse('recruitment:application_review', args=[pk]) + '#documents')


@role_required(roles.HR)
@require_POST
def retry(request, pk, job_pk):
    application = _reviewable(request, pk)
    job = get_object_or_404(AIJob, pk=job_pk, application=application)
    try:
        services.retry(request.user, job)
    except ValidationError as exc:
        _flash(request, exc)
    else:
        messages.success(request, 'Extraction queued again.')
    return redirect(_job_url(pk, job_pk))


def _item(application, item_pk):
    return get_object_or_404(ExtractionItem.objects.select_related('extraction__job'),
                             pk=item_pk, extraction__job__application=application)


@role_required(roles.HR)
@require_POST
def item_action(request, pk, item_pk):
    application = _reviewable(request, pk)
    item = _item(application, item_pk)
    action = request.POST.get('action')
    reason = request.POST.get('reason', '').strip()
    try:
        if action == 'accept':
            services.accept_item(request.user, item)
            messages.success(request, 'Item accepted as an application fact.')
        elif action == 'link':
            fact = get_object_or_404(application.facts, pk=request.POST.get('fact'))
            services.link_item(request.user, item, fact, reason=reason)
            messages.success(request, 'Item linked as evidence for the existing fact.')
        elif action == 'reject':
            services.reject_item(request.user, item, reason=reason)
            messages.success(request, 'Item rejected.')
        else:
            raise ValidationError('Choose an action.')
    except ValidationError as exc:
        _flash(request, exc)
    return redirect(_job_url(pk, item.extraction.job_id, f'#item-{item.pk}'))


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def item_edit(request, pk, item_pk):
    application = _reviewable(request, pk)
    item = _item(application, item_pk)
    if item.item_type not in services.REVIEWABLE_TYPES:
        raise Http404
    ValueForm = fact_schema.value_form_class(item.item_type)

    class EditForm(ValueForm, CorrectionReasonForm):
        pass

    form = EditForm(request.POST or None,
                    initial=fact_schema.initial_from_value(item.item_type, item.proposed_value))
    if request.method == 'POST' and form.is_valid():
        value = fact_schema.to_value(item.item_type, form.cleaned_data)
        try:
            services.accept_item(request.user, item, value=value,
                                 reason=form.cleaned_data['reason'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Item accepted with your corrections; the original '
                                      'proposal is kept.')
            return redirect(_job_url(pk, item.extraction.job_id, f'#item-{item.pk}'))
    return render(request, 'ai_processing/item_edit.html', {
        'application': application, 'item': item, 'form': form,
        'label': FactType(item.item_type).label,
        'cancel_url': _job_url(pk, item.extraction.job_id, f'#item-{item.pk}')})
