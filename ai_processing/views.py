"""HR pages for AI extraction (13.3): run or retry a job, read the extracted
text beside the proposed items, and accept, correct, link, or reject each item.
Also the advisory qualification checks (13.4). Everything is advisory and
labeled as such."""

from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from accounts.access import role_required
from documents.models import DocumentVersion
from recruitment import application_selectors as access
from recruitment import application_services as applications
from recruitment import fact_schema
from recruitment.application_forms import CorrectionReasonForm
from recruitment.models import Application, FactType

from . import matching, screening, services
from .models import AIJob, AIScreening, ExtractionItem, ItemReviewStatus, JobStatus, JobType

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


# --------------------------------------------------------------------------
# Advisory qualification checks (13.4)
# --------------------------------------------------------------------------

@role_required(roles.HR)
@require_POST
def run_checks(request, pk):
    application = _reviewable(request, pk)
    try:
        screening_record, created = screening.run_checks(request.user, application)
    except ValidationError as exc:
        _flash(request, exc)
        return redirect(reverse('recruitment:application_review', args=[pk]) + '#qualifications')
    messages.success(request, 'Qualification checks done. They are advisory: record the '
                              'official outcome yourself.' if created else
                     'Nothing changed since the last check; showing it again.')
    return redirect('ai_processing:screening', pk=pk, screening_pk=screening_record.pk)


@role_required(*STAFF_ROLES)
def screening_detail(request, pk, screening_pk):
    application = _visible(request.user, pk)
    record = get_object_or_404(AIScreening.objects.select_related(
        'job__requested_by', 'input_snapshot__requirement_version'), pk=screening_pk,
        job__application=application)
    outcomes = applications.qualification_outcomes(application)
    rows = []
    for result in record.results.select_related('standard').order_by('created_at'):
        rows.append({
            'result': result,
            'facts': [(f, fact_schema.display_items(f.fact_type, f.value))
                      for f in screening.evidence_facts(application, result)],
            'official': outcomes.get(str(result.standard_id)),
        })
    return render(request, 'ai_processing/screening.html', {
        'application': application, 'screening': record, 'rows': rows,
        'current': not record.job.is_invalidated and screening.is_current(record),
        'can_review': access.can_review(request.user, application),
        'can_run': access.can_review(request.user, application)
        and not screening.problems(application),
        'outcome_labels': fact_schema.OUTCOME_LABELS,
    })


# --------------------------------------------------------------------------
# HR workspace: extraction results across the HR officer's vacancies
# --------------------------------------------------------------------------

_LIVE = Q(invalidated_at__isnull=True)
_FINISHED = Q(status__in=[JobStatus.COMPLETED, JobStatus.NEEDS_REVIEW])
RESULT_TABS = [
    # (key, label, filter on the annotated job queryset)
    ('review', 'Waiting for HR review', _LIVE & _FINISHED & Q(pending_items__gt=0)),
    ('running', 'Queued or processing',
     _LIVE & Q(status__in=[JobStatus.QUEUED, JobStatus.PROCESSING])),
    ('failed', 'Failed', _LIVE & Q(status=JobStatus.FAILED)),
    ('done', 'Reviewed', _LIVE & _FINISHED & Q(pending_items=0)),
    ('all', 'All, including invalidated', Q()),
]


@role_required(roles.HR)
def results(request):
    jobs = (AIJob.objects.filter(job_type=JobType.DOCUMENT_EXTRACTION,
                                 application__in=access.visible_applications(request.user)
                                 .values('pk'))
            .select_related('application__applicant__user', 'application__vacancy',
                            'document_version__document__document_type')
            .annotate(item_count=Count('extraction__items', distinct=True),
                      pending_items=Count('extraction__items', distinct=True,
                                          filter=Q(extraction__items__review_status=
                                                   ItemReviewStatus.PENDING))))
    tabs = [(key, label, jobs.filter(condition).count()) for key, label, condition in RESULT_TABS]
    show = request.GET.get('show')
    if show not in dict((k, c) for k, _, c in RESULT_TABS):
        show = 'review'
    condition = next(c for k, _, c in RESULT_TABS if k == show)
    page = Paginator(jobs.filter(condition).order_by('-queued_at'), 25) \
        .get_page(request.GET.get('page'))
    return render(request, 'ai_processing/results.html', {
        'tabs': tabs, 'show': show, 'page': page,
        'enabled': settings.AI_EXTRACTION_ENABLED,
    })


# --------------------------------------------------------------------------
# SBERT competency matching (13.5)
# --------------------------------------------------------------------------

def _matching_url(pk, job_pk):
    return reverse('ai_processing:matches', args=[pk, job_pk])


@role_required(roles.HR)
@require_POST
def run_matching(request, pk):
    application = _reviewable(request, pk)
    try:
        job, created = matching.queue_matching(request.user, application)
    except ValidationError as exc:
        _flash(request, exc)
        return redirect(reverse('recruitment:application_review', args=[pk]) + '#competencies')
    messages.success(request, 'Competency matching queued; the AI worker runs it shortly.'
                     if created else 'Nothing changed since the last matching run.')
    return redirect(_matching_url(pk, job.pk))


@role_required(roles.HR)
@require_POST
def retry_matching(request, pk, job_pk):
    application = _reviewable(request, pk)
    job = get_object_or_404(AIJob, pk=job_pk, application=application,
                            job_type=JobType.COMPETENCY_MATCH)
    try:
        services.retry(request.user, job)
    except ValidationError as exc:
        _flash(request, exc)
    else:
        messages.success(request, 'Competency matching queued again.')
    return redirect(_matching_url(pk, job_pk))


@role_required(*STAFF_ROLES)
def matches(request, pk, job_pk):
    application = _visible(request.user, pk)
    job = get_object_or_404(AIJob.objects.select_related('requested_by'), pk=job_pk,
                            application=application, job_type=JobType.COMPETENCY_MATCH)
    can_review = access.can_review(request.user, application)
    return render(request, 'ai_processing/matches.html', {
        'application': application, 'job': job, 'groups': matching.by_competency(job),
        'current': not job.is_invalidated and matching.is_current(job),
        'attempts': job.attempts.order_by('attempt_number'),
        'can_review': can_review,
        'can_run': can_review and not matching.problems(application),
        'runtime': (job.configuration or {}).get('runtime') or {},
    })
