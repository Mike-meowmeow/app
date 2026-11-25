from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from accounts.access import role_required
from core.files import private_file_response
from documents import storage
from recruitment.selectors import public_vacancies

from . import services
from .forms import RECORD_FORMS, PersonalInformationForm, ProfileDocumentForm
from .models import DEGREE_RANK, MAX_FILES_PER_RECORD, ProfileDocument


def _record_or_404(user, kind, pk):
    if kind not in services.RECORD_KINDS:
        raise Http404
    try:
        return services.get_record(user, kind, pk)
    except services.RECORD_KINDS[kind][0].DoesNotExist:
        raise Http404  # other applicants' records are indistinguishable from missing


@role_required(roles.APPLICANT)
def profile(request):
    profile = services.get_profile(request.user)
    education = sorted(profile.educational_records.all(),
                       key=lambda e: (DEGREE_RANK.get(e.degree_level, 0), e.created_at))
    files = services.documents_by_record(profile)
    sections = []
    for kind, label, records in [
            ('education', 'Education', education),
            ('experience', 'Work experience', profile.work_experiences.all()),
            ('training', 'Training', profile.trainings.all()),
            ('eligibility', 'Eligibility', profile.eligibilities.all()),
            ('license', 'Professional licenses', profile.professional_licenses.all())]:
        types = services.allowed_document_types(kind)
        rows = [(r, files.get(r.pk, []), ProfileDocumentForm(types=types, prefix=str(r.pk)))
                for r in records]
        sections.append((kind, label, rows, types))
    return render(request, 'applicants/profile.html', {
        'profile': profile,
        'missing': profile.missing_information(),
        'sections': sections,
        'max_files': MAX_FILES_PER_RECORD,
        'open_vacancy_count': public_vacancies().count(),
    })


@role_required(roles.APPLICANT)
@require_POST
def attach_document(request, kind, pk):
    record = _record_or_404(request.user, kind, pk)
    form = ProfileDocumentForm(request.POST, request.FILES,
                               types=services.allowed_document_types(kind), prefix=str(pk))
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
    else:
        try:
            document = services.attach_document(request.user, kind, record.pk,
                                                form.cleaned_data['document_type'],
                                                form.cleaned_data['file'])
        except ValidationError as exc:
            for message in exc.messages:
                messages.error(request, message)
        else:
            messages.success(request, f'Attached {document.file_name}. It will be added to '
                                      'applications for vacancies that ask for this document.')
    return redirect(f"{reverse('applicants:profile')}#record-{pk}")


@role_required(roles.APPLICANT)
@require_POST
def remove_document(request, pk):
    try:
        document = services.get_profile_document(request.user, pk)
    except ProfileDocument.DoesNotExist:
        raise Http404
    services.remove_document(request.user, document.pk)
    messages.success(request, f'Removed {document.file_name}. Applications that already include '
                              'it keep their copy.')
    return redirect(f"{reverse('applicants:profile')}#record-{document.record_id}")


@role_required(roles.APPLICANT)
def document_file(request, pk):
    """The applicant's own attached file (others get 404)."""
    try:
        document = services.get_profile_document(request.user, pk)
    except ProfileDocument.DoesNotExist:
        raise Http404
    path = storage.path_for(document.storage_key)
    if not path.exists() or document.malware_status == 'quarantined':
        raise Http404
    inline = request.GET.get('inline') == '1'
    return private_file_response(path, as_attachment=not inline, filename=document.file_name,
                                 content_type=document.mime_type)


@role_required(roles.APPLICANT)
@require_http_methods(['GET', 'POST'])
def edit_personal(request):
    profile = services.get_profile(request.user)
    form = PersonalInformationForm(
        request.POST or None,
        initial=PersonalInformationForm.initial_for(request.user, profile))
    if request.method == 'POST' and form.is_valid():
        try:
            services.update_personal_information(request.user, form.cleaned_data)
        except ValidationError as exc:
            for field, errors in exc.message_dict.items():
                form.add_error(field if field in form.fields else None, errors)
        else:
            messages.success(request, 'Personal information saved.')
            return redirect('applicants:profile')
    return render(request, 'form_page.html', {
        'form': form, 'title': 'Personal information', 'submit_label': 'Save',
        'cancel_url': 'applicants:profile'})


@role_required(roles.APPLICANT)
@require_http_methods(['GET', 'POST'])
def edit_record(request, kind, pk=None):
    if kind not in services.RECORD_KINDS:
        raise Http404
    instance = _record_or_404(request.user, kind, pk) if pk else None
    form = RECORD_FORMS[kind](request.POST or None, instance=instance)
    label = services.RECORD_KINDS[kind][1]
    types = services.allowed_document_types(kind)
    attached = list(ProfileDocument.objects.filter(
        applicant__user=request.user, record_kind=kind, record_id=instance.pk)
        .select_related('document_type')) if instance else []
    can_attach = bool(types) and len(attached) < MAX_FILES_PER_RECORD
    file_form = ProfileDocumentForm(request.POST or None, request.FILES or None, types=types,
                                    optional=True, prefix='file') if can_attach else None
    if request.method == 'POST' and form.is_valid() and (file_form is None or file_form.is_valid()):
        upload = file_form.cleaned_data.get('file') if file_form else None
        try:
            # The entry and its file are saved together: a rejected file saves neither.
            with transaction.atomic():
                record = services.save_record(request.user, kind, form.save(commit=False))
                if upload:
                    services.attach_document(request.user, kind, record.pk,
                                             file_form.cleaned_data['document_type'] or types[0],
                                             upload)
        except ValidationError as exc:
            if hasattr(exc, 'error_dict'):
                for field, errors in exc.message_dict.items():
                    form.add_error(field if field in form.fields else None, errors)
            else:
                (file_form or form).add_error('file' if file_form else None, exc.messages)
            if instance is None:
                form.instance._state.adding = True  # the rolled-back entry was never saved
        else:
            messages.success(request, f'{label} saved' + (f' with {upload.name}.' if upload
                                                          else '.'))
            return redirect(f"{reverse('applicants:profile')}#record-{record.pk}")
    return render(request, 'applicants/record_form.html', {
        'form': form, 'file_form': file_form, 'attached': attached, 'types': types,
        'max_files': MAX_FILES_PER_RECORD,
        'title': f'{"Edit" if pk else "Add"} {label.lower()}'})


@role_required(roles.APPLICANT)
@require_POST
def delete_record(request, kind, pk):
    _record_or_404(request.user, kind, pk)
    services.delete_record(request.user, kind, pk)
    messages.success(request, f'{services.RECORD_KINDS[kind][1]} removed.')
    return redirect('applicants:profile')
