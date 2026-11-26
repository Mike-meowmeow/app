from django import forms
from django.contrib.auth import get_user_model

from accounts import roles
from applicants.models import DegreeLevel
from core.credentials import KINDS as CREDENTIAL_KINDS, CredentialNameInput
from core.forms import BootstrapFormMixin
from core.models import Department, RecruitmentType
from documents.models import DocumentType

from .models import (AssignmentRole, CriterionType, Position, PositionDocumentRequirement,
                     Vacancy, VacancyDocumentRequirement, VacancyStatus)

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
        self.is_new = self.instance._state.adding
        if self.is_new:
            # New vacancies start from the position catalog: the title is copied
            # from the chosen position and the description is pre-filled.
            del self.fields['position_title']
            self.fields['position'] = forms.ModelChoiceField(
                queryset=Position.objects.filter(is_active=True), label='Position',
                empty_label='Choose a position',
                help_text='Its title, default description and default requirements are '
                          'copied into this vacancy. Manage positions in the Position '
                          'catalog.')
            self.fields['position'].widget.attrs.update({'class': 'form-select',
                                                         'data-position-select': ''})
            self.fields['description'].required = False
            self.fields['description'].help_text = (
                "Pre-filled from the position; edit it for this vacancy if needed. Left "
                "blank, the position's default description is used.")
            self.fields['description'].widget.attrs['data-position-description'] = ''
            self.order_fields(['position'])
        elif self.instance.position_id:
            del self.fields['position_title']
        self.is_published = not self.is_new and \
            self.instance.status == VacancyStatus.PUBLISHED
        if self.is_published:
            for name in LOCKED_WHEN_PUBLISHED:
                if name in self.fields:
                    self.fields[name].disabled = True
                    self.fields[name].help_text = 'Fixed after publication.'
        else:
            del self.fields['reason']

    def clean(self):
        data = super().clean()
        position = data.get('position') if self.is_new else None
        if position is not None:
            # Model validation runs on the instance; give it the values the
            # service will copy from the position.
            self.instance.position_title = position.title
            if not (data.get('description') or '').strip():
                data['description'] = position.description
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
                           label='Required eligibility or license name',
                           widget=CredentialNameInput(CREDENTIAL_KINDS, switch='criterion_type'),
                           help_text='Choose from the list, or choose Other and type it if it is not listed.')
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


class ApplyPositionForm(BootstrapFormMixin, forms.Form):
    """Explicit reload of catalog defaults into a never-published draft."""

    position = forms.ModelChoiceField(queryset=Position.objects.none(), label='Position',
                                      empty_label='Choose a position')
    confirm = forms.BooleanField(
        label='Replace the current title, description and draft requirements with this '
              "position's defaults")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['position'].queryset = Position.objects.filter(is_active=True)


class PositionForm(BootstrapFormMixin, forms.ModelForm):
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}),
                             help_text='Optional; recorded in the audit trail. Existing '
                                       'vacancies are not changed by catalog edits.')

    class Meta:
        model = Position
        fields = ('title', 'description')
        labels = {'description': 'Default job description'}
        widgets = {'description': forms.Textarea(attrs={'rows': 6})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance._state.adding:
            del self.fields['reason']

    def clean_title(self):
        title = ' '.join(self.cleaned_data['title'].split())
        clash = Position.objects.filter(title__iexact=title)
        if self.instance.pk:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise forms.ValidationError('A position with this title already exists.')
        return title

    def validate_unique(self):
        pass  # clean_title checks case-insensitively; the service rechecks under lock


class PositionDocumentForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = PositionDocumentRequirement
        fields = ('document_type', 'is_required', 'max_files', 'instructions')
        widgets = {'instructions': forms.Textarea(attrs={'rows': 2})}
        labels = {'is_required': 'Required document', 'max_files': 'Maximum files'}

    def __init__(self, *args, position=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = DocumentType.objects.filter(is_active=True)
        if position is not None:
            qs = qs.exclude(position_requirements__position=position)
        self.fields['document_type'].queryset = qs


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
