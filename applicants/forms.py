from django import forms
from django.utils import timezone

from core.forms import BootstrapFormMixin

from .models import (Applicant, CivilStatus, EducationalRecord, Eligibility, ProfessionalLicense,
                     Sex, Training, WorkExperience, contact_number_validator)

DATE_WIDGET = forms.DateInput(attrs={'type': 'date'}, format='%Y-%m-%d')


class PersonalInformationForm(BootstrapFormMixin, forms.Form):
    first_name = forms.CharField(max_length=150)
    middle_name = forms.CharField(max_length=150, required=False,
                                  help_text='Leave blank if you have no middle name.')
    last_name = forms.CharField(max_length=150)
    contact_no = forms.CharField(label='Contact number', max_length=30,
                                 validators=[contact_number_validator])
    birthdate = forms.DateField(widget=DATE_WIDGET)
    sex = forms.ChoiceField(choices=[('', '---------')] + Sex.choices)
    civil_status = forms.ChoiceField(choices=[('', '---------')] + CivilStatus.choices)
    address = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}))

    def clean_birthdate(self):
        birthdate = self.cleaned_data['birthdate']
        if birthdate > timezone.localdate():
            raise forms.ValidationError('This date cannot be in the future.')
        return birthdate

    @classmethod
    def initial_for(cls, user, profile: Applicant):
        return {'first_name': user.first_name, 'middle_name': user.middle_name or '',
                'last_name': user.last_name, 'contact_no': profile.contact_no,
                'birthdate': profile.birthdate, 'sex': profile.sex,
                'civil_status': profile.civil_status, 'address': profile.address}


class RecordForm(BootstrapFormMixin, forms.ModelForm):
    """Base for repeatable records. `applicant` is never a form field; the
    service always sets it to the signed-in applicant."""


class EducationForm(RecordForm):
    class Meta:
        model = EducationalRecord
        fields = ('degree_level', 'program', 'institution', 'completion_status', 'completed_on')
        widgets = {'completed_on': DATE_WIDGET}


class WorkExperienceForm(RecordForm):
    class Meta:
        model = WorkExperience
        fields = ('position', 'organization', 'start_date', 'end_date', 'duties')
        widgets = {'start_date': DATE_WIDGET, 'end_date': DATE_WIDGET,
                   'duties': forms.Textarea(attrs={'rows': 3})}


class TrainingForm(RecordForm):
    class Meta:
        model = Training
        fields = ('title', 'provider', 'hours', 'start_date', 'end_date')
        widgets = {'start_date': DATE_WIDGET, 'end_date': DATE_WIDGET}


class EligibilityForm(RecordForm):
    class Meta:
        model = Eligibility
        fields = ('eligibility_type', 'rating', 'date_obtained')
        widgets = {'date_obtained': DATE_WIDGET}


class LicenseForm(RecordForm):
    class Meta:
        model = ProfessionalLicense
        fields = ('profession', 'license_number', 'issuing_body', 'issued_on', 'expires_on')
        widgets = {'issued_on': DATE_WIDGET, 'expires_on': DATE_WIDGET}


RECORD_FORMS = {
    'education': EducationForm,
    'experience': WorkExperienceForm,
    'training': TrainingForm,
    'eligibility': EligibilityForm,
    'license': LicenseForm,
}
