from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django import forms as dj_forms
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from accounts.access import role_required
from documents import services as documents
from documents.models import Document, DocumentType, DocumentVersion
from documents.storage import FileRejected

from . import application_selectors as access
from . import application_services as services
from . import decision_services as decisions
from . import fact_schema, selectors, stage_tracker
from .application_forms import (ApplicationFilterForm, ApplicationSearchForm, CorrectionReasonForm,
                                CorrectionRequestForm, ExceptionForm, ManualFactForm,
                                OutcomeForm, ReasonForm, ReplaceForm, SubmitForm, UploadForm,
                                VerificationForm, WithdrawForm)
from .models import (Application, ApplicationFact, ApplicationStatus, CorrectionRequest,
                     FactReviewStatus, FactType, QualificationStandard, Vacancy, VacancyStatus,
                     WorkflowVisibility)

STAFF_ROLES = (roles.HR, roles.APPROVER, roles.DEPARTMENT_HEAD)


def _flash(request, exc):
    for message in getattr(exc, 'messages', [str(exc)]):
        messages.error(request, message)


def _own_or_404(user, pk):
    try:
        return access.own_applications(user).get(pk=pk)
    except Application.DoesNotExist:
        raise Http404


def _visible_or_404(user, pk):
    try:
        return access.visible_applications(user).get(pk=pk)
    except Application.DoesNotExist:
        raise Http404


def _snapshot_view(data):
    """Snapshot data shaped for templates."""
    if not data:
        return None
    return {
        'identity': fact_schema.display_items('identity', data.get('identity', {})),
        'sections': [(FactType(kind).label, [fact_schema.display_items(kind, item)
                                             for item in data.get(kind, [])])
                     for kind in fact_schema.PROFILE_KINDS],
        'documents': data.get('documents', []),
        'requirement_version': data.get('requirement_version'),
    }


# ==========================================================================
# Applicant
# ==========================================================================

@role_required(roles.APPLICANT)
@require_POST
def apply(request, vacancy_pk):
    vacancy = get_object_or_404(Vacancy, pk=vacancy_pk, status=VacancyStatus.PUBLISHED)
    try:
        application, created = services.start_application(request.user, vacancy)
    except ValidationError as exc:
        _flash(request, exc)
        return redirect('recruitment:public_detail', pk=vacancy_pk)
    if created:
        messages.success(request, 'Draft application started. Upload your documents, review, '
                                  'then submit before the deadline.')
        _import_from_profile(request, application)
    return redirect('recruitment:my_application', pk=application.pk)


def _import_from_profile(request, application, report_none=False):
    try:
        imported, skipped = documents.import_profile_documents(request.user, application)
    except (FileRejected, ValidationError) as exc:
        _flash(request, exc)
        return
    if imported:
        messages.success(request, f'Added {len(imported)} file{"s" if len(imported) != 1 else ""} '
                                  'from your profile: '
                                  + ', '.join(v.file_name for v in imported) + '.')
    elif report_none:
        messages.info(request, 'No new files from your profile match this vacancy\'s '
                               'document list.')
    for message in skipped:
        messages.warning(request, message)


@role_required(roles.APPLICANT)
@require_POST
def import_profile_documents(request, pk):
    application = _own_or_404(request.user, pk)
    _import_from_profile(request, application, report_none=True)
    return redirect('recruitment:my_application', pk=pk)


@role_required(roles.APPLICANT)
def my_applications(request):
    applications = list(access.own_applications(request.user))
    for app in applications:
        app.public = decisions.PublicStatus(decisions.applicant_status(app))
    return render(request, 'recruitment/applications/my_list.html', {
        'applications': applications})


@role_required(roles.APPLICANT)
def my_application(request, pk):
    application = _own_or_404(request.user, pk)
    checklist = services.document_checklist(application)
    correction = application.open_correction_request()
    is_draft = application.status == ApplicationStatus.DRAFT
    can_upload = is_draft or (correction is not None and correction.window_open())
    snapshot = application.latest_snapshot()
    if snapshot:
        preview_data = snapshot.data
    else:
        requirement = services.requirements_for(application)
        preview_data = services.build_snapshot_data(application, requirement) if requirement \
            else None
    preview = _snapshot_view(preview_data)
    profile_files_available = 0
    if is_draft:
        imported = application.documents.exclude(profile_document=None) \
            .values_list('profile_document_id', flat=True)
        profile_files_available = application.applicant.profile_documents.filter(
            document_type__in=[r.document_type_id for r, _ in checklist]
        ).exclude(pk__in=list(imported)).count()
    return render(request, 'recruitment/applications/applicant_detail.html', {
        'profile_files_available': profile_files_available,
        'application': application, 'checklist': checklist, 'correction': correction,
        'is_draft': is_draft, 'can_upload': can_upload, 'preview': preview,
        'problems': services.submission_problems(application) if is_draft else
                    services.correction_problems(application, correction) if correction else [],
        'upload_form': UploadForm(requirements=[r for r, _ in checklist]),
        'replace_form': ReplaceForm(), 'submit_form': SubmitForm(),
        'history': application.workflow_logs.filter(visibility=WorkflowVisibility.APPLICANT),
        'can_withdraw': application.status in services.WITHDRAWABLE,
        'correction_types': set(correction.document_type_codes) if correction else set(),
        # Stage 7: only released information reaches the applicant.
        'public_status': decisions.PublicStatus(decisions.applicant_status(application)),
        'released_decisions': decisions.released_decisions(application),
        'interviews': application.interviews.all(),
    })


@role_required(roles.APPLICANT)
@require_POST
def upload_document(request, pk):
    application = _own_or_404(request.user, pk)
    replaces = None
    if request.POST.get('replaces'):
        replaces = get_object_or_404(Document, pk=request.POST['replaces'],
                                     application=application)
        form = ReplaceForm(request.POST, request.FILES)
    else:
        form = UploadForm(request.POST, request.FILES,
                          requirements=[r for r, _ in services.document_checklist(application)])
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect('recruitment:my_application', pk=pk)
    try:
        version = documents.upload(request.user, application,
                                   form.cleaned_data.get('document_type'),
                                   form.cleaned_data['file'], replaces=replaces)
    except (FileRejected, ValidationError) as exc:
        _flash(request, exc)
    else:
        messages.success(request, f'Uploaded {version.file_name} (version {version.version}).')
    return redirect('recruitment:my_application', pk=pk)


@role_required(roles.APPLICANT)
@require_POST
def remove_document(request, pk, document_pk):
    application = _own_or_404(request.user, pk)
    document = get_object_or_404(Document, pk=document_pk, application=application)
    try:
        documents.remove_draft_document(request.user, document)
    except ValidationError as exc:
        _flash(request, exc)
    else:
        messages.success(request, 'Document removed from the draft.')
    return redirect('recruitment:my_application', pk=pk)


@role_required(roles.APPLICANT)
@require_POST
def submit_application(request, pk):
    application = _own_or_404(request.user, pk)
    form = SubmitForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Please confirm before submitting.')
        return redirect('recruitment:my_application', pk=pk)
    try:
        application, already = services.submit(request.user, application)
    except ValidationError as exc:
        _flash(request, exc)
    else:
        if already:
            messages.info(request, 'This application was already submitted.')
        else:
            messages.success(request, f'Application {application.reference} submitted on '
                                      f'{application.submitted_at:%B %d, %Y %I:%M %p} UTC. '
                                      'Keep this reference for your records.')
    return redirect('recruitment:my_application', pk=pk)


@role_required(roles.APPLICANT)
@require_POST
def resubmit_corrections(request, pk):
    application = _own_or_404(request.user, pk)
    try:
        services.resubmit_corrections(request.user, application)
    except ValidationError as exc:
        _flash(request, exc)
    else:
        messages.success(request, 'Corrections submitted. HR will review them.')
    return redirect('recruitment:my_application', pk=pk)


@role_required(roles.APPLICANT)
@require_http_methods(['GET', 'POST'])
def withdraw(request, pk):
    application = _own_or_404(request.user, pk)
    form = WithdrawForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            services.withdraw(request.user, application, reason=form.cleaned_data['reason'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Your application was withdrawn.')
            return redirect('recruitment:my_application', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Withdraw application {application.reference}',
        'intro': f'{application.vacancy.position_title}. Withdrawn applications remain on '
                 'record (including the official applicant roster) but stop progressing.',
        'submit_label': 'Withdraw application', 'cancel_url': 'recruitment:my_application',
        'cancel_arg': pk})


# ==========================================================================
# Staff
# ==========================================================================

@role_required(*STAFF_ROLES)
def vacancy_applications(request, pk):
    try:
        vacancy = selectors.visible_vacancies(request.user).get(pk=pk)
    except Vacancy.DoesNotExist:
        raise Http404
    form = ApplicationFilterForm(request.GET or None)
    filters = form.cleaned_data if form.is_valid() else {}
    apps = access.search(access.visible_applications(request.user).filter(vacancy=vacancy),
                         q=filters.get('q', ''), status=filters.get('status', ''))
    page = Paginator(apps.order_by('submitted_at'), 25).get_page(request.GET.get('page'))
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'recruitment/applications/staff_list.html', {
        'vacancy': vacancy, 'form': form, 'page': page, 'query': query.urlencode()})


@role_required(*STAFF_ROLES)
def application_search(request):
    """Permission-aware search across every vacancy the user may see (FR-015)."""
    vacancies = selectors.visible_vacancies(request.user)
    form = ApplicationSearchForm(request.GET or None, vacancies=vacancies)
    filters = form.cleaned_data if form.is_valid() else {}
    apps = access.search(access.visible_applications(request.user).select_related(
        'vacancy__department'), **{k: v for k, v in filters.items() if v not in (None, '')})
    page = Paginator(apps.order_by('-submitted_at'), 25).get_page(request.GET.get('page'))
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'recruitment/applications/search.html', {
        'form': form, 'page': page, 'query': query.urlencode()})


@role_required(roles.HR)
def verification_queue(request):
    return render(request, 'recruitment/applications/queue.html', {
        'versions': documents.pending_verifications(request.user)})


@role_required(*STAFF_ROLES)
def review(request, pk):
    application = _visible_or_404(request.user, pk)
    can_review = access.can_review(request.user, application)
    requirement = services.requirements_for(application)
    facts = list(application.facts.select_related('reviewed_by')
                 .prefetch_related('evidence__document_version__document__document_type')
                 .order_by('fact_type', 'created_at'))
    current = [f for f in facts if f.is_current
               and f.fact_type != FactType.QUALIFICATION_ASSESSMENT]
    for f in current:
        f.no_evidence = services.fact_needs_evidence(f)
    outcomes = services.qualification_outcomes(application)
    standards = list(requirement.qualification_standards.all()) if requirement else []
    snapshot = application.latest_snapshot()
    versions = list(DocumentVersion.objects.filter(document__application=application)
                    .select_related('document__document_type')
                    .order_by('document__document_type__name', 'document_id', '-version'))
    from ai_processing import services as ai
    ai_jobs = ai.jobs_by_version(application)
    for v in versions:
        v.ai_job = ai_jobs.get(v.pk)
        v.ai_can_run = (settings.AI_EXTRACTION_ENABLED and can_review
                        and (v.ai_job is None or v.ai_job.is_invalidated)
                        and not ai.eligibility_problems(v))
    from ai_processing import matching
    from ai_processing import screening as checks
    check = checks.latest(application)
    check_results = checks.results_by_standard(check)
    inputs_hash = checks.inputs_hash(application)  # for the outcome locks
    match_job = matching.latest(application)
    return render(request, 'recruitment/applications/review.html', {
        'match_job': match_job,
        'match_current': match_job is not None and matching.is_current(match_job),
        'match_groups': matching.by_competency(match_job, top=3)
        if match_job and match_job.status == 'completed' else [],
        'can_run_matching': can_review and not matching.problems(application),
        'show_matching': matching.enabled() or match_job is not None,
        'check': check, 'check_current': check is not None and checks.is_current(check),
        'can_run_checks': can_review and not checks.problems(application),
        'show_checks': checks.enabled() or check is not None,
        'show_ai': settings.AI_EXTRACTION_ENABLED or bool(ai_jobs),
        'application': application, 'can_review': can_review,
        'stages': stage_tracker.build(application),
        'snapshot': snapshot, 'snapshot_view': _snapshot_view(snapshot.data if snapshot else None),
        'checklist': services.document_checklist(application),
        'facts': [(f, fact_schema.display_items(f.fact_type, f.value)) for f in current],
        'superseded_count': sum(1 for f in facts if not f.is_current),
        'no_evidence_count': sum(1 for f in current if f.no_evidence),
        'standards': [(s, outcomes.get(str(s.pk)), check_results.get(str(s.pk)),
                       _check_facts(application, check_results.get(str(s.pk))),
                       services.outcome_locked(application, outcomes.get(str(s.pk)), inputs_hash))
                      for s in standards],
        # Just ran the checks: open every standard's reasoning.
        'expand_checks': request.GET.get('checked') == '1',
        'outcome_labels': fact_schema.OUTCOME_LABELS,
        'versions': versions,
        'corrections_requested': application.correction_requests.select_related('requested_by'),
        'history': application.workflow_logs.select_related('actor'),
        'field_corrections': application.corrections.select_related('actor'),
        'reason_form': ReasonForm(),
        'statuses': ApplicationStatus,
        'can_start_review': application.status == ApplicationStatus.SUBMITTED,
        'can_request_correction': application.status in (ApplicationStatus.SUBMITTED,
                                                         ApplicationStatus.UNDER_REVIEW)
                                  and application.open_correction_request() is None,
        'can_reopen': application.status in services.CONCLUDED,
    })


def _check_facts(application, result):
    """Facts an advisory check result used, ready for display."""
    if result is None:
        return []
    from ai_processing import screening as checks
    return [(f, fact_schema.display_items(f.fact_type, f.value))
            for f in checks.evidence_facts(application, result)]


def _reviewable_or_403(request, pk):
    application = _visible_or_404(request.user, pk)
    if not access.can_review(request.user, application):
        raise PermissionDenied
    return application


@role_required(roles.HR)
@require_POST
def start_review(request, pk):
    application = _reviewable_or_403(request, pk)
    try:
        services.start_review(request.user, application)
    except ValidationError as exc:
        _flash(request, exc)
    return redirect('recruitment:application_review', pk=pk)


@role_required(roles.HR)
@require_POST
def reopen(request, pk):
    application = _reviewable_or_403(request, pk)
    try:
        services.reopen(request.user, application, reason=request.POST.get('reason', '').strip())
    except ValidationError as exc:
        _flash(request, exc)
    else:
        messages.success(request, 'Application reopened for review.')
    return redirect('recruitment:application_review', pk=pk)


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def verify_document(request, pk, version_pk):
    application = _reviewable_or_403(request, pk)
    version = get_object_or_404(DocumentVersion, pk=version_pk, document__application=application)
    form = VerificationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            documents.verify(request.user, version, status=form.cleaned_data['status'],
                             notes=form.cleaned_data['notes'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f'{version.file_name}: {form.cleaned_data["status"]}.')
            next_url = request.POST.get('next', '')
            if next_url and url_has_allowed_host_and_scheme(next_url, {request.get_host()}):
                return redirect(next_url)
            return redirect('recruitment:application_review', pk=pk)
    return render(request, 'recruitment/applications/verify.html', {
        'application': application, 'version': version, 'form': form,
        'history': version.verifications.select_related('reviewed_by'),
        'latest': version.verifications.select_related('reviewed_by').order_by('-reviewed_at')
        .first(),
        'next': request.GET.get('next', '')})


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def request_correction(request, pk):
    application = _reviewable_or_403(request, pk)
    requirement = services.requirements_for(application)
    types = DocumentType.objects.filter(
        pk__in=requirement.document_requirements.values('document_type')) if requirement \
        else DocumentType.objects.none()
    form = CorrectionRequestForm(request.POST or None, document_types=types)
    if request.method == 'POST' and form.is_valid():
        try:
            services.request_correction(request.user, application,
                                        message=form.cleaned_data['message'],
                                        document_types=form.cleaned_data['document_types'],
                                        deadline_at=form.cleaned_data['deadline_at'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Correction requested. The applicant can now update the '
                                      'requested items until the deadline.')
            return redirect('recruitment:application_review', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Request correction · {application.reference}',
        'intro': f'{application.applicant} · {application.vacancy.position_title}',
        'submit_label': 'Send correction request', 'cancel_url': 'recruitment:application_review',
        'cancel_arg': pk})


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def correction_exception(request, pk, request_pk):
    application = _reviewable_or_403(request, pk)
    correction = get_object_or_404(CorrectionRequest, pk=request_pk, application=application)
    form = ExceptionForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        try:
            services.grant_correction_exception(request.user, correction,
                                                deadline_at=form.cleaned_data['deadline_at'],
                                                reason=form.cleaned_data['reason'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Correction window extended.')
            return redirect('recruitment:application_review', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': 'Late correction exception',
        'intro': f'Current deadline: {correction.deadline_at:%Y-%m-%d %H:%M} UTC. '
                 'An exception needs an authorized reason and is audited.',
        'submit_label': 'Grant exception', 'cancel_url': 'recruitment:application_review',
        'cancel_arg': pk})


def _fact_or_404(application, fact_pk):
    return get_object_or_404(ApplicationFact, pk=fact_pk, application=application)


@role_required(roles.HR)
@require_POST
def fact_review(request, pk, fact_pk):
    application = _reviewable_or_403(request, pk)
    fact = _fact_or_404(application, fact_pk)
    try:
        if request.POST.get('relevance'):
            services.set_relevance(request.user, fact, request.POST['relevance'],
                                   reason=request.POST.get('reason', ''))
        elif request.POST.get('change'):
            changed = services.change_fact_decision(request.user, fact,
                                                    decision=request.POST.get('decision', ''),
                                                    reason=request.POST.get('reason', ''))
            messages.success(request, f'Decision changed to '
                                      f'{changed.get_review_status_display()}.')
        else:
            services.review_fact(request.user, fact, decision=request.POST.get('decision', ''),
                                 reason=request.POST.get('reason', '').strip())
    except ValidationError as exc:
        _flash(request, exc)
    return redirect(f"{redirect('recruitment:application_review', pk=pk).url}#facts")


@role_required(roles.HR)
@require_POST
def fact_evidence(request, pk, fact_pk):
    application = _reviewable_or_403(request, pk)
    fact = _fact_or_404(application, fact_pk)
    version = get_object_or_404(DocumentVersion, pk=request.POST.get('version'),
                                document__application=application)
    try:
        services.link_evidence(request.user, fact, version,
                               location={'note': request.POST.get('location', '').strip()}
                               if request.POST.get('location') else None)
    except ValidationError as exc:
        _flash(request, exc)
    return redirect(f"{redirect('recruitment:application_review', pk=pk).url}#facts")


@role_required(roles.HR)
@require_POST
def fact_unlink(request, pk, fact_pk, link_pk):
    application = _reviewable_or_403(request, pk)
    fact = _fact_or_404(application, fact_pk)
    from documents.models import FactEvidence
    link = get_object_or_404(FactEvidence, pk=link_pk, application_fact=fact)
    try:
        services.unlink_evidence(request.user, link, reason=request.POST.get('reason', ''))
    except ValidationError as exc:
        _flash(request, exc)
    else:
        messages.success(request, 'Document unlinked. You can link the right one now.')
    return redirect(f"{redirect('recruitment:application_review', pk=pk).url}#facts")


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def fact_correct(request, pk, fact_pk):
    application = _reviewable_or_403(request, pk)
    fact = _fact_or_404(application, fact_pk)
    if fact.fact_type not in fact_schema.FACT_FIELDS:
        raise Http404
    ValueForm = fact_schema.value_form_class(fact.fact_type)

    class CorrectForm(ValueForm, CorrectionReasonForm):
        pass

    form = CorrectForm(request.POST or None,
                       initial=fact_schema.initial_from_value(fact.fact_type, fact.value))
    if request.method == 'POST' and form.is_valid():
        try:
            services.correct_fact(request.user, fact,
                                  fact_schema.to_value(fact.fact_type, form.cleaned_data),
                                  reason=form.cleaned_data['reason'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Correction recorded; the previous value is kept in '
                                      'history.')
            return redirect('recruitment:application_review', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Correct {fact.get_fact_type_display().lower()}',
        'intro': f'{application.reference} · the original value is preserved.',
        'submit_label': 'Save correction', 'cancel_url': 'recruitment:application_review',
        'cancel_arg': pk})


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def fact_add(request, pk):
    application = _reviewable_or_403(request, pk)
    fact_type = request.POST.get('fact_type') or request.GET.get('fact_type')
    if fact_type not in fact_schema.PROFILE_KINDS:
        return render(request, 'form_page.html', {
            'form': ManualFactForm(), 'title': 'Add a fact manually', 'method': 'get',
            'intro': 'Choose the type of qualification to record from a verified document.',
            'submit_label': 'Continue', 'cancel_url': 'recruitment:application_review',
            'cancel_arg': pk})
    ValueForm = fact_schema.value_form_class(fact_type)
    versions = DocumentVersion.objects.filter(document__application=application) \
        .select_related('document__document_type')

    class AddForm(ValueForm, ReasonForm):
        evidence = dj_forms.ModelChoiceField(queryset=versions, required=False,
                                             label='Source document')
        fact_type = dj_forms.CharField(widget=dj_forms.HiddenInput)

    form = AddForm(request.POST or None, initial={'fact_type': fact_type})
    form.fields['reason'].label = 'Source and reason'
    if request.method == 'POST' and form.is_valid():
        try:
            services.add_manual_fact(request.user, application, fact_type,
                                     fact_schema.to_value(fact_type, form.cleaned_data),
                                     reason=form.cleaned_data['reason'],
                                     evidence_version=form.cleaned_data['evidence'])
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Fact added.')
            return redirect('recruitment:application_review', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Add {FactType(fact_type).label.lower()}',
        'intro': application.reference,
        'submit_label': 'Add fact', 'cancel_url': 'recruitment:application_review',
        'cancel_arg': pk})


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def qualification_outcome(request, pk, standard_pk):
    application = _reviewable_or_403(request, pk)
    requirement = services.requirements_for(application)
    standard = get_object_or_404(QualificationStandard, pk=standard_pk,
                                 requirement_version=requirement)
    accepted = [f for f in application.facts.filter(review_status=FactReviewStatus.ACCEPTED)
                .exclude(fact_type__in=[FactType.QUALIFICATION_ASSESSMENT, FactType.IDENTITY])]
    labelled = [(f, f'{f.get_fact_type_display()}: ' + ', '.join(
        str(v) for _, v in fact_schema.display_items(f.fact_type, f.value)[:3])) for f in accepted]
    current = services.qualification_outcomes(application).get(str(standard.pk))
    # A recorded outcome is locked until something it was based on changes;
    # a mistake is fixed with Change outcome (?change=1), which needs a reason.
    locked = services.outcome_locked(application, current)
    if locked and request.GET.get('change') != '1':
        messages.info(request, 'This outcome is already recorded and nothing has changed since. '
                               'Use Change outcome, with a reason, if it was a mistake.')
        return redirect(f"{redirect('recruitment:application_review', pk=pk).url}#qualifications")
    initial = {'outcome': current.value['outcome'], 'reason': current.value.get('reason', ''),
               'supporting_facts': current.value.get('supporting_fact_ids', [])} if current else {}
    # "Record outcome starting from this check": the advisory result fills the
    # form; HR still reads, edits, and submits it.
    from ai_processing import screening as checks
    check_result = None if locked else checks.usable_result(application, standard,
                                                           request.GET.get('check'))
    intro = f'{standard.get_criterion_type_display()}: {standard.description}'
    if check_result:
        accepted_ids = {str(f.pk) for f in accepted}
        initial = {'outcome': check_result.result,
                   'reason': 'Advisory check: ' + ' '.join(check_result.explanation_lines),
                   'supporting_facts': [i for i in check_result.evidence_fact_ids
                                        if i in accepted_ids]}
        intro += (f'. Filled in from the advisory check ({check_result.get_result_display()}); '
                  'review and edit before recording.')
    if locked:
        intro += ('. Changing a recorded outcome: give the reason it was a mistake. The earlier '
                  'outcome stays in the history.')
    form = OutcomeForm(request.POST or None, facts=labelled, initial=initial, change=locked)
    if request.method == 'POST' and form.is_valid():
        chosen = [f for f in accepted if str(f.pk) in form.cleaned_data['supporting_facts']]
        try:
            services.record_qualification_outcome(
                request.user, application, standard, outcome=form.cleaned_data['outcome'],
                reason=form.cleaned_data['reason'], supporting_facts=chosen,
                check_result=check_result,
                change_reason=form.cleaned_data.get('change_reason', ''))
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, 'Qualification outcome changed.' if locked
                             else 'Qualification outcome recorded.')
            return redirect(f"{redirect('recruitment:application_review', pk=pk).url}#qualifications")
    return render(request, 'form_page.html', {
        'form': form, 'title': 'Qualification outcome', 'intro': intro,
        'submit_label': 'Change outcome' if locked else 'Record outcome',
        'cancel_url': 'recruitment:application_review',
        'cancel_arg': pk})
