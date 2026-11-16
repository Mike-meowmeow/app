from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from accounts.access import role_required
from recruitment.selectors import public_vacancies

from . import services
from .forms import RECORD_FORMS, PersonalInformationForm
from .models import DEGREE_RANK


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
    return render(request, 'applicants/profile.html', {
        'profile': profile,
        'missing': profile.missing_information(),
        'sections': [
            ('education', 'Education', education),
            ('experience', 'Work experience', profile.work_experiences.all()),
            ('training', 'Training', profile.trainings.all()),
            ('eligibility', 'Eligibility', profile.eligibilities.all()),
            ('license', 'Professional licenses', profile.professional_licenses.all()),
        ],
        'open_vacancy_count': public_vacancies().count(),
    })


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
    if request.method == 'POST' and form.is_valid():
        try:
            services.save_record(request.user, kind, form.save(commit=False))
        except ValidationError as exc:
            for field, errors in exc.message_dict.items():
                form.add_error(field if field in form.fields else None, errors)
        else:
            messages.success(request, f'{label} saved.')
            return redirect('applicants:profile')
    return render(request, 'form_page.html', {
        'form': form, 'title': f'{"Edit" if pk else "Add"} {label.lower()}',
        'submit_label': 'Save', 'cancel_url': 'applicants:profile'})


@role_required(roles.APPLICANT)
@require_POST
def delete_record(request, kind, pk):
    _record_or_404(request.user, kind, pk)
    services.delete_record(request.user, kind, pk)
    messages.success(request, f'{services.RECORD_KINDS[kind][1]} removed.')
    return redirect('applicants:profile')
