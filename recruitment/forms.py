from django import forms
from django.contrib.auth import get_user_model

from accounts import roles
from applicants.models import DegreeLevel
from core.forms import BootstrapFormMixin
from core.models import Department, RecruitmentType
from documents.models import DocumentType

from .models import AssignmentRole, CriterionType, Vacancy, VacancyDocumentRequirement, \
    VacancyStatus

DATETIME_WIDGET = forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')
LOCKED_WHEN_PUBLISHED = ('department', 'recruitment_type', 'position_title', 'opens_at')


class VacancyForm(BootstrapFormMixin, forms.ModelForm):
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}),
                             help_text='Required when changing a published vacancy; '
                                       'recorded in the audit trail.')

    class Meta:
        model = Vacancy
        fields = ('position_title', 'department', 'recruitment_type', 'description',
                  'positions_available', 'opens_at', 'deadline_at')
        widgets = {'opens_at': DATETIME_WIDGET, 'deadline_at': DATETIME_WIDGET,
                   'description': forms.Textarea(attrs={'rows': 5})}
        labels = {'opens_at': 'Applications open (Asia/Manila time)',
                  'deadline_at': 'Application deadline (Asia/Manila time)'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['department'].queryset = Department.objects.filter(is_active=True)
        self.fields['recruitment_type'].queryset = RecruitmentType.objects.filter(is_active=True)
        self.is_published = bool(self.instance.pk) and \
            self.instance.status == VacancyStatus.PUBLISHED
        if self.is_published:
            for name in LOCKED_WHEN_PUBLISHED:
                self.fields[name].disabled = True
                self.fields[name].help_text = 'Fixed after publication.'
        else:
            del self.fields['reason']

    def clean(self):
        data = super().clean()
        if self.is_published and not data.get('reason'):
            self.add_error('reason', 'Explain why the published vacancy is changing.')
        opens, deadline = data.get('opens_at'), data.get('deadline_at')
        if opens and deadline and opens > deadline:
            self.add_error('deadline_at', 'The deadline must be after the opening date.')
        return data


class DocumentRequirementForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = VacancyDocumentRequirement
        fields = ('document_type', 'is_required', 'max_files', 'instructions')
        widgets = {'instructions': forms.Textarea(attrs={'rows': 2})}
        labels = {'is_required': 'Required document', 'max_files': 'Maximum files'}

    def __init__(self, *args, draft=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = DocumentType.objects.filter(is_active=True)
        if draft is not None:
            qs = qs.exclude(vacancy_requirements__requirement_version=draft)
        self.fields['document_type'].queryset = qs


class QualificationStandardForm(BootstrapFormMixin, forms.Form):
    criterion_type = forms.ChoiceField(choices=CriterionType.choices, label='Criterion')
    degree_level = forms.ChoiceField(choices=[('', '---------')] + DegreeLevel.choices,
                                     required=False, label='Minimum degree (education)')
    amount = forms.DecimalField(required=False, min_value=0, max_digits=7, decimal_places=2,
                                label='Minimum years (experience) or hours (training)')
    name = forms.CharField(required=False, max_length=200,
                           label='Required eligibility or license name')
    field = forms.CharField(required=False, max_length=200,
                            label='Field or specialization (optional)')
    description = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}),
                                  help_text='How the requirement reads to applicants, e.g. '
                                            "\"Master's degree in Mathematics or related field\".")

    REQUIRED_BY_CRITERION = {
        CriterionType.EDUCATION: 'degree_level',
        CriterionType.EXPERIENCE: 'amount',
        CriterionType.TRAINING: 'amount',
        CriterionType.ELIGIBILITY: 'name',
        CriterionType.LICENSE: 'name',
    }

    def clean(self):
        data = super().clean()
        needed = self.REQUIRED_BY_CRITERION.get(data.get('criterion_type'))
        if needed and data.get(needed) in (None, ''):
            self.add_error(needed, 'Required for this criterion.')
        return data


class CompetencyForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=200, label='Subject or competency')
    description = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}))


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}))


class AssignmentForm(BootstrapFormMixin, forms.Form):
    user = forms.ModelChoiceField(queryset=get_user_model().objects.none(), label='Staff member')
    assignment_role = forms.ChoiceField(choices=AssignmentRole.choices, label='Assign as')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = get_user_model().objects.filter(
            is_active=True, groups__name__in=[roles.HR, roles.DEPARTMENT_HEAD]
        ).distinct().order_by('last_name', 'first_name')
        self.fields['user'].label_from_instance = \
            lambda u: f'{u.full_name or u.username} ({u.username})'


class VacancyFilterForm(forms.Form):
    q = forms.CharField(required=False, label='Search')
    department = forms.ModelChoiceField(queryset=Department.objects.all(), required=False)
    recruitment_type = forms.ModelChoiceField(queryset=RecruitmentType.objects.all(),
                                              required=False)
    status = forms.ChoiceField(choices=[('', 'Any status')] + VacancyStatus.choices,
                               required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            css = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
            field.widget.attrs['class'] = css
