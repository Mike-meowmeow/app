from datetime import timedelta

from django import forms
from django.conf import settings
from django.utils import timezone

from core.forms import BootstrapFormMixin
from documents.models import DocumentType, VerificationStatus

from . import fact_schema
from .models import ApplicationStatus, FactType

DATETIME_WIDGET = forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')


class UploadForm(BootstrapFormMixin, forms.Form):
    document_type = forms.ModelChoiceField(queryset=DocumentType.objects.none(),
                                           label='Document category')
    file = forms.FileField()

    def __init__(self, *args, requirements=None, **kwargs):
        super().__init__(*args, **kwargs)
        if requirements is not None:
            self.fields['document_type'].queryset = DocumentType.objects.filter(
                pk__in=[r.document_type_id for r in requirements])


class ReplaceForm(BootstrapFormMixin, forms.Form):
    file = forms.FileField(label='Replacement file')


class SubmitForm(BootstrapFormMixin, forms.Form):
    confirm = forms.BooleanField(
        label='I confirm that the information and documents I am submitting are true and '
              'complete.',
        error_messages={'required': 'Please confirm before submitting.'})


class WithdrawForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}),
                             label='Reason (optional)')
    confirm = forms.BooleanField(
        label='I understand that withdrawing stops this application. It stays on record.',
        error_messages={'required': 'Please confirm the withdrawal.'})


# What each verification outcome means (shown under the choices; fixed text).
VERIFICATION_MEANINGS = (
    '<ul class="mb-0 ps-3">'
    '<li><strong>Verified</strong>: the right document, readable, and it shows what it '
    'should.</li>'
    '<li><strong>Incomplete</strong>: the right document, but pages or details are missing; '
    'ask the applicant for a complete copy.</li>'
    '<li><strong>Invalid</strong>: the right kind of document, but not acceptable (for '
    'example expired, unsigned, or the wrong form version).</li>'
    '<li><strong>Rejected</strong>: irrelevant or unusable (for example the wrong file, a '
    'blank page, or junk).</li></ul>')


class VerificationForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(choices=[c for c in VerificationStatus.choices
                                        if c[0] != VerificationStatus.PENDING],
                               widget=forms.RadioSelect, label='Outcome',
                               help_text=VERIFICATION_MEANINGS)
    notes = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}),
                            label='Reason / comments',
                            help_text='Required unless Verified. Internal to HR; tell the '
                                      'applicant what to fix through a correction request.')

    def clean(self):
        data = super().clean()
        if data.get('status') and data['status'] != VerificationStatus.VERIFIED \
                and not data.get('notes', '').strip():
            self.add_error('notes', 'A reason is required for this outcome.')
        return data


class CorrectionRequestForm(BootstrapFormMixin, forms.Form):
    message = forms.CharField(widget=forms.Textarea(attrs={'rows': 4}),
                              label='What the applicant must correct',
                              help_text='Shown to the applicant.')
    document_types = forms.ModelMultipleChoiceField(
        queryset=DocumentType.objects.none(), required=False,
        widget=forms.CheckboxSelectMultiple,
        label='Documents that must be replaced')
    deadline_at = forms.DateTimeField(widget=DATETIME_WIDGET,
                                      label='Correction deadline (Asia/Manila time)')

    def __init__(self, *args, document_types=None, **kwargs):
        super().__init__(*args, **kwargs)
        if document_types is not None:
            self.fields['document_types'].queryset = document_types
        if not self.is_bound:
            self.initial.setdefault('deadline_at', timezone.localtime(
                timezone.now() + timedelta(days=settings.CORRECTION_WINDOW_DAYS)))

    def clean_deadline_at(self):
        value = self.cleaned_data['deadline_at']
        if value <= timezone.now():
            raise forms.ValidationError('The deadline must be in the future.')
        return value


class ExceptionForm(BootstrapFormMixin, forms.Form):
    deadline_at = forms.DateTimeField(widget=DATETIME_WIDGET,
                                      label='New correction deadline (Asia/Manila time)')
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}),
                             label='Reason for the exception')


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}))


class CorrectionReasonForm(BootstrapFormMixin, forms.Form):
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}),
                             label='Reason for the correction',
                             help_text='Recorded with the old and new values.')


class ManualFactForm(BootstrapFormMixin, forms.Form):
    fact_type = forms.ChoiceField(choices=[(k, v) for k, v in FactType.choices
                                           if k in fact_schema.PROFILE_KINDS],
                                  label='Qualification type')


class OutcomeForm(BootstrapFormMixin, forms.Form):
    outcome = forms.ChoiceField(choices=fact_schema.QUALIFICATION_OUTCOMES,
                                widget=forms.RadioSelect)
    reason = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}),
                             help_text='Required for Does Not Meet and Needs Review.')
    supporting_facts = forms.MultipleChoiceField(required=False,
                                                 widget=forms.CheckboxSelectMultiple,
                                                 label='Supporting accepted facts')

    def __init__(self, *args, facts=(), change=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supporting_facts'].choices = [(str(f.pk), label) for f, label in facts]
        if change:  # the outcome is locked: changing it needs a reason
            self.fields['change_reason'] = forms.CharField(
                label='Why the recorded outcome was a mistake',
                widget=forms.Textarea(attrs={'rows': 2}),
                help_text='Kept in the history and the audit log; the HR Head / Approver '
                          'is notified.')
            self.fields['change_reason'].widget.attrs.setdefault('class', 'form-control')

    def clean(self):
        data = super().clean()
        if data.get('outcome') in ('does_not_meet', 'needs_review') and not data.get('reason'):
            self.add_error('reason', 'Explain this outcome.')
        return data


class ApplicationFilterForm(forms.Form):
    q = forms.CharField(required=False, label='Search')
    status = forms.ChoiceField(required=False, choices=[('', 'Any status')] + [
        c for c in ApplicationStatus.choices if c[0] != ApplicationStatus.DRAFT])

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['q'].widget.attrs.update({'class': 'form-control',
                                              'placeholder': 'Name, email, program, or APP-'})
        self.fields['status'].widget.attrs['class'] = 'form-select'


class ApplicationSearchForm(ApplicationFilterForm):
    """Cross-vacancy search (FR-015): name/ID/email/program, vacancy,
    department, education, qualification review result, and status."""

    vacancy = forms.ModelChoiceField(queryset=None, required=False, empty_label='Any vacancy')
    department = forms.ModelChoiceField(queryset=None, required=False,
                                        empty_label='Any department')
    degree_level = forms.ChoiceField(required=False, label='Education')
    outcome = forms.ChoiceField(required=False, label='Qualification review')

    def __init__(self, *args, vacancies, **kwargs):
        super().__init__(*args, **kwargs)
        from applicants.models import DegreeLevel
        from core.models import Department
        self.fields['vacancy'].queryset = vacancies.order_by('position_title')
        self.fields['department'].queryset = Department.objects.filter(
            pk__in=vacancies.values('department_id'))
        self.fields['degree_level'].choices = [('', 'Any education')] + list(DegreeLevel.choices)
        self.fields['outcome'].choices = [('', 'Any result'), ('not_assessed', 'Not assessed')] \
            + list(fact_schema.OUTCOME_LABELS.items())
        for name in ('vacancy', 'department', 'degree_level', 'outcome'):
            self.fields[name].widget.attrs['class'] = 'form-select'


# --------------------------------------------------------------------------
# Decisions (Stage 7)
# --------------------------------------------------------------------------

class DecisionForm(BootstrapFormMixin, forms.Form):
    outcome = forms.ChoiceField(widget=forms.RadioSelect)
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), label='Justification',
                             help_text='Internal record of why the decision was made.')
    decision_at = forms.DateTimeField(widget=DATETIME_WIDGET, input_formats=['%Y-%m-%dT%H:%M'],
                                      label='Decision date', initial=timezone.localtime)
    committee = forms.ModelChoiceField(queryset=None, required=False)
    references = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}),
                                 label='Supporting references',
                                 help_text='One per line (minutes, memo numbers, documents).')
    appointment_reference = forms.CharField(max_length=100, required=False,
                                            label='Appointment reference',
                                            help_text='e.g. appointment or contract number.')
    appointment_at = forms.DateTimeField(widget=DATETIME_WIDGET, required=False,
                                         input_formats=['%Y-%m-%dT%H:%M'],
                                         label='Appointment date')

    def __init__(self, *args, decision_type, outcomes, committees, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['outcome'].choices = [(o.value, o.label) for o in outcomes]
        self.fields['committee'].queryset = committees
        if decision_type == 'committee_recommendation':
            self.fields['committee'].required = True
            self.fields['committee'].empty_label = None
        else:
            self.fields['committee'].help_text = 'Optional.'
        if decision_type != 'appointment':
            del self.fields['appointment_reference']
            del self.fields['appointment_at']
        else:
            self.fields['appointment_reference'].required = True
            self.fields['appointment_at'].required = True


class CorrectDecisionForm(BootstrapFormMixin, forms.Form):
    correction_reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}),
                                        label='Reason for the correction')
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), label='Justification')
    decision_at = forms.DateTimeField(widget=DATETIME_WIDGET, input_formats=['%Y-%m-%dT%H:%M'],
                                      label='Decision date')
    references = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}),
                                 label='Supporting references', help_text='One per line.')
    appointment_reference = forms.CharField(max_length=100, required=False,
                                            label='Appointment reference')
    appointment_at = forms.DateTimeField(widget=DATETIME_WIDGET, required=False,
                                         input_formats=['%Y-%m-%dT%H:%M'],
                                         label='Appointment date')

    def __init__(self, *args, decision, **kwargs):
        kwargs.setdefault('initial', {
            'reason': decision.reason, 'decision_at': timezone.localtime(decision.decision_at),
            'references': '\n'.join(decision.supporting_references.get('references', [])),
            'appointment_reference': decision.appointment_reference,
            'appointment_at': decision.appointment_at and timezone.localtime(
                decision.appointment_at)})
        super().__init__(*args, **kwargs)
        if decision.decision_type != 'appointment':
            del self.fields['appointment_reference']
            del self.fields['appointment_at']


class ReleaseForm(BootstrapFormMixin, forms.Form):
    note = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}),
                           label='Message to the applicant',
                           help_text='Shown to the applicant with the decision. Do not include '
                                     'internal evaluation notes.')
