from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from accounts.access import role_required
from core.files import private_file_response
from recruitment.models import Vacancy

from . import builder, layout, services
from .forms import CommentsForm, EntryForm, GenerateForm, ReturnForm, SignatoriesForm
from .models import (ApplicantRoster, ExportFormat, RosterEntry, RosterExport, RosterStatus,
                     RosterTemplate)

ROSTER_ROLES = (roles.HR, roles.APPROVER)


def _flash(request, exc):
    for message in getattr(exc, 'messages', [str(exc)]):
        messages.error(request, message)


def _vacancy_or_404(user, pk):
    vacancy = get_object_or_404(Vacancy.objects.select_related('department', 'recruitment_type'),
                                pk=pk)
    if not services.can_view(user, vacancy):
        raise Http404  # do not reveal rosters of unrelated vacancies
    return vacancy


def _roster_or_404(user, pk):
    roster = get_object_or_404(ApplicantRoster.objects.select_related(
        'vacancy__department', 'vacancy__recruitment_type', 'template', 'prepared_by',
        'approved_by', 'released_by'), pk=pk)
    if not services.can_view(user, roster.vacancy):
        raise Http404
    return roster


@role_required(*ROSTER_ROLES)
def vacancy_rosters(request, pk):
    vacancy = _vacancy_or_404(request.user, pk)
    can_prepare = services.can_prepare(request.user, vacancy)
    rosters = vacancy.rosters.select_related('prepared_by', 'approved_by')
    active = rosters.filter(status__in=services.ACTIVE).first()
    form = GenerateForm(request.POST or None, vacancy=vacancy)
    if request.method == 'POST':
        if not can_prepare:
            raise PermissionDenied
        if form.is_valid():
            try:
                roster = services.generate(request.user, vacancy,
                                           cutoff_at=form.cleaned_data['cutoff_at'])
            except ValidationError as exc:
                _flash(request, exc)
            else:
                messages.success(request, f'Roster version {roster.version} generated with '
                                          f'{roster.entries.count()} applicant(s).')
                return redirect('matrix:roster_detail', pk=roster.pk)
    return render(request, 'matrix/vacancy_rosters.html', {
        'vacancy': vacancy, 'rosters': rosters, 'active': active, 'form': form,
        'can_prepare': can_prepare, 'problems': services.configuration_problems(vacancy),
        'submitted_count': vacancy.applications.filter(submitted_at__isnull=False).count()})


@role_required(*ROSTER_ROLES)
def roster_detail(request, pk):
    roster = _roster_or_404(request.user, pk)
    can_prepare = services.can_prepare(request.user, roster.vacancy)
    can_approve = services.can_approve(request.user)
    entries = list(roster.entries.select_related('application__applicant__user')
                   .order_by('sequence_number'))
    columns = [(c, roster.header_snapshot.get('column_labels', layout.DEFAULT_LABELS).get(c, c))
               for c in layout.COLUMNS]
    rows = [(e, [(c, e.exported_values.get(c, ''), e.flagged_columns.get(c)) for c in
                 layout.COLUMNS]) for e in entries]
    outdated = roster.requires_regeneration or (
        roster.status in services.ACTIVE and bool(services.outdated_reason(roster)))
    return render(request, 'matrix/roster_detail.html', {
        'roster': roster, 'rows': rows, 'columns': columns, 'groups':
            roster.header_snapshot.get('groups', []),
        'can_edit': can_prepare and roster.is_editable,
        'can_prepare': can_prepare, 'can_approve': can_approve,
        'is_preparer': roster.prepared_by_id == request.user.pk,
        'self_approval_allowed': services.self_approval_allowed(),
        'outdated': outdated, 'outdated_reason': services.outdated_reason(roster)
            if roster.status in services.OUTDATABLE else '',
        'problems': services.approval_problems(roster) if roster.is_editable else [],
        'unresolved': sum(1 for e in entries if e.needs_acknowledgment),
        'events': roster.events.select_related('actor'),
        'exports': roster.exports.select_related('generated_by'),
        'flag_labels': builder.FLAG_LABELS, 'legend': builder.LEGEND,
        'comments_form': CommentsForm(), 'return_form': ReturnForm(),
        'statuses': RosterStatus,
        'can_export_draft': roster.status in services.ACTIVE,
        'can_release': can_approve and roster.status == RosterStatus.APPROVED
                       and not roster.released_at and not roster.requires_regeneration,
        'can_new_version': can_prepare and roster.is_locked and not
                           roster.vacancy.rosters.filter(status__in=services.ACTIVE).exists(),
    })


def _preparable_or_403(request, pk):
    roster = _roster_or_404(request.user, pk)
    if not services.can_prepare(request.user, roster.vacancy):
        raise PermissionDenied
    return roster


@role_required(roles.HR)
@require_POST
def regenerate(request, pk):
    roster = _preparable_or_403(request, pk)
    try:
        services.regenerate(request.user, roster)
        messages.success(request, f'Version {roster.version} rebuilt from current application '
                                  'information.')
    except ValidationError as exc:
        _flash(request, exc)
    return redirect('matrix:roster_detail', pk=pk)


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def entry_edit(request, pk, entry_pk):
    roster = _preparable_or_403(request, pk)
    entry = get_object_or_404(RosterEntry.objects.select_related('application__applicant__user'),
                              pk=entry_pk, roster=roster)
    if not roster.is_editable:
        messages.error(request, 'This roster version can no longer be edited.')
        return redirect('matrix:roster_detail', pk=pk)
    form = EntryForm(request.POST or None, initial={'remarks': entry.remarks})
    if not entry.needs_acknowledgment:
        form.fields.pop('acknowledge')
    if request.method == 'POST' and form.is_valid():
        try:
            services.update_entry(request.user, entry, remarks=form.cleaned_data['remarks'],
                                  acknowledge=form.cleaned_data.get('acknowledge', False))
        except ValidationError as exc:
            _flash(request, exc)
        else:
            messages.success(request, f'Row {entry.sequence_number} updated.')
            return redirect('matrix:roster_detail', pk=pk)
    labels = roster.header_snapshot.get('column_labels', layout.DEFAULT_LABELS)
    flags = [(labels.get(c, c), entry.exported_values.get(c, ''), builder.FLAG_LABELS[f])
             for c, f in entry.flagged_columns.items() if f != builder.NOT_APPLICABLE]
    return render(request, 'matrix/entry_edit.html', {
        'roster': roster, 'entry': entry, 'form': form, 'flags': flags,
        'documents': entry.review_flags.get('documents')})


@role_required(roles.HR)
@require_http_methods(['GET', 'POST'])
def signatories(request, pk):
    roster = _preparable_or_403(request, pk)
    if not roster.is_editable:
        messages.error(request, 'Signatories can only be changed while the version is editable.')
        return redirect('matrix:roster_detail', pk=pk)
    form = SignatoriesForm(request.POST or None,
                           initial=SignatoriesForm.from_snapshot(roster.signatory_snapshot))
    if request.method == 'POST' and form.is_valid():
        try:
            services.update_signatories(request.user, roster, form.snapshot())
        except ValidationError as exc:
            _flash(request, exc)
        else:
            messages.success(request, 'Signatories saved for this roster version.')
            return redirect('matrix:roster_detail', pk=pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Signatories - roster version {roster.version}',
        'intro': 'Printed below the last applicant row. Saved with this version only; '
                 'approved versions keep the signatories they were approved with.',
        'submit_label': 'Save signatories', 'cancel_url': 'matrix:roster_detail',
        'cancel_arg': pk})


@role_required(roles.HR)
@require_POST
def submit_for_approval(request, pk):
    roster = _preparable_or_403(request, pk)
    problems = services.submit_for_approval(request.user, roster)
    for problem in problems:
        messages.error(request, problem)
    if not problems:
        messages.success(request, f'Version {roster.version} sent for approval.')
    return redirect('matrix:roster_detail', pk=pk)


@role_required(roles.HR)
@require_POST
def new_version(request, pk):
    roster = _preparable_or_403(request, pk)
    try:
        new = services.generate(request.user, roster.vacancy, cutoff_at=roster.cutoff_at)
    except ValidationError as exc:
        _flash(request, exc)
        return redirect('matrix:roster_detail', pk=pk)
    messages.success(request, f'Version {new.version} generated from current information '
                              f'(same cutoff). Version {roster.version} is unchanged.')
    return redirect('matrix:roster_detail', pk=new.pk)


@role_required(roles.APPROVER)
@require_POST
def approve(request, pk):
    roster = _roster_or_404(request.user, pk)
    form = CommentsForm(request.POST)
    comments = form.cleaned_data['comments'] if form.is_valid() else ''
    problems = services.approve(request.user, roster, comments=comments)
    for problem in problems:
        messages.error(request, problem)
    if not problems:
        messages.success(request, f'Version {roster.version} approved. Release it to produce '
                                  'the official files.')
    return redirect('matrix:roster_detail', pk=pk)


@role_required(roles.APPROVER)
@require_POST
def return_roster(request, pk):
    roster = _roster_or_404(request.user, pk)
    form = ReturnForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Explain what needs correction before returning the roster.')
        return redirect('matrix:roster_detail', pk=pk)
    try:
        services.return_for_correction(request.user, roster,
                                       comments=form.cleaned_data['comments'])
        messages.success(request, f'Version {roster.version} returned to HR.')
    except ValidationError as exc:
        _flash(request, exc)
    return redirect('matrix:roster_detail', pk=pk)


@role_required(roles.APPROVER)
@require_POST
def release(request, pk):
    roster = _roster_or_404(request.user, pk)
    try:
        services.release(request.user, roster)
        messages.success(request, f'Version {roster.version} released. The official Excel and '
                                  'PDF files are ready below.')
    except services.ExportFailed as exc:
        messages.error(request, str(exc))
    except ValidationError as exc:
        _flash(request, exc)
    return redirect('matrix:roster_detail', pk=pk)


@role_required(*ROSTER_ROLES)
@require_POST
def export_draft(request, pk, fmt):
    roster = _roster_or_404(request.user, pk)
    if fmt not in ExportFormat.values:
        raise Http404
    try:
        services.create_export(request.user, roster, fmt)
        messages.success(request, f'Draft {ExportFormat(fmt).label} file generated.')
    except services.ExportFailed as exc:
        messages.error(request, str(exc))
    except ValidationError as exc:
        _flash(request, exc)
    return redirect('matrix:roster_detail', pk=pk)


CONTENT_TYPES = {
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'pdf': 'application/pdf',
}


@role_required(*ROSTER_ROLES)
def download(request, pk):
    export = get_object_or_404(RosterExport.objects.select_related('roster__vacancy'), pk=pk)
    if not services.can_download(request.user, export):
        raise Http404
    path = services.export_path(export)
    if not path.exists():
        raise Http404
    services.record_download(request.user, export)
    return private_file_response(path, filename=export.file_name,
                                 content_type=CONTENT_TYPES[export.format])


@role_required(roles.APPROVER)
def pending(request):
    return render(request, 'matrix/pending.html', {
        'rosters': ApplicantRoster.objects.filter(status=RosterStatus.PENDING_APPROVAL)
        .select_related('vacancy__department', 'prepared_by').order_by('updated_at'),
        'released': ApplicantRoster.objects.filter(status=RosterStatus.APPROVED)
        .select_related('vacancy__department', 'approved_by').order_by('-approved_at')[:20]})


@role_required(roles.APPROVER, roles.SYSTEM_ADMIN)
def templates(request):
    return render(request, 'matrix/templates.html', {
        'templates': RosterTemplate.objects.select_related('recruitment_type', 'approved_by'),
        'can_manage': roles.has_role(request.user, *services.TEMPLATE_MANAGERS)})


@role_required(roles.APPROVER, roles.SYSTEM_ADMIN)
@require_http_methods(['GET', 'POST'])
def template_signatories(request, pk):
    template = get_object_or_404(RosterTemplate, pk=pk)
    form = SignatoriesForm(request.POST or None,
                           initial=SignatoriesForm.from_snapshot(template.signatory_defaults))
    if request.method == 'POST' and form.is_valid():
        services.update_template_signatories(request.user, template, form.snapshot())
        messages.success(request, 'Default signatories saved. Existing roster versions keep '
                                  'their own signatories.')
        return redirect('matrix:templates')
    return render(request, 'form_page.html', {
        'form': form, 'title': f'Default signatories - {template.name} v{template.version}',
        'intro': 'Used for new roster versions. Past and approved versions are not changed.',
        'submit_label': 'Save defaults', 'cancel_url': 'matrix:templates'})


@role_required(roles.APPROVER, roles.SYSTEM_ADMIN)
@require_POST
def template_approve(request, pk):
    template = get_object_or_404(RosterTemplate, pk=pk)
    try:
        services.approve_template(request.user, template)
        messages.success(request, f'{template.name} v{template.version} approved; earlier '
                                  'versions for this recruitment type were retired.')
    except ValidationError as exc:
        _flash(request, exc)
    return redirect('matrix:templates')
