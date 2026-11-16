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


class VerificationForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(choices=[c for c in VerificationStatus.choices
                                        if c[0] != VerificationStatus.PENDING],
                               widget=forms.RadioSelect, label='Outcome')
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

    def __init__(self, *args, facts=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['supporting_facts'].choices = [(str(f.pk), label) for f, label in facts]

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
