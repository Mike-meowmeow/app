from django import forms
from django.utils import timezone

from core.forms import BootstrapFormMixin

DATETIME_WIDGET = forms.DateTimeInput(attrs={'type': 'datetime-local'}, format='%Y-%m-%dT%H:%M')


class GenerateForm(BootstrapFormMixin, forms.Form):
    cutoff_at = forms.DateTimeField(
        label='Cutoff', widget=DATETIME_WIDGET, input_formats=['%Y-%m-%dT%H:%M'],
        help_text='Every application first submitted on or before this time is included, '
                  'whatever its current status. Defaults to the application deadline.')

    def __init__(self, *args, vacancy=None, **kwargs):
        super().__init__(*args, **kwargs)
        if vacancy is not None and not self.is_bound:
            default = vacancy.deadline_at or timezone.now()
            self.initial['cutoff_at'] = timezone.localtime(default).strftime('%Y-%m-%dT%H:%M')


class EntryForm(BootstrapFormMixin, forms.Form):
    remarks = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 3}),
                              label='Remarks (column T)',
                              help_text='HR-approved remarks printed on the roster, e.g. the '
                                        'application status or a deficiency note.')
    acknowledge = forms.BooleanField(
        required=False, label='I reviewed the flagged values in this row',
        help_text='Acknowledging records your name and time. Values stay marked as '
                  'unverified on the roster until HR verifies them on the application.')


class SignatoriesForm(BootstrapFormMixin, forms.Form):
    prepared_name = forms.CharField(max_length=150, label='Prepared by: name')
    prepared_position = forms.CharField(max_length=150, label='Prepared by: position')
    noted_name = forms.CharField(max_length=150, label='Noted by: name')
    noted_position = forms.CharField(max_length=150, label='Noted by: position')

    @classmethod
    def from_snapshot(cls, data):
        data = data or {}
        prepared, noted = data.get('prepared_by') or {}, data.get('noted_by') or {}
        return {'prepared_name': prepared.get('name', ''),
                'prepared_position': prepared.get('position', ''),
                'noted_name': noted.get('name', ''), 'noted_position': noted.get('position', '')}

    def snapshot(self):
        d = self.cleaned_data
        return {'schema_version': 1,
                'prepared_by': {'name': d['prepared_name'].strip(),
                                'position': d['prepared_position'].strip()},
                'noted_by': {'name': d['noted_name'].strip(),
                             'position': d['noted_position'].strip()}}


class CommentsForm(BootstrapFormMixin, forms.Form):
    comments = forms.CharField(required=False, widget=forms.Textarea(attrs={'rows': 2}),
                               label='Comments')


class ReturnForm(BootstrapFormMixin, forms.Form):
    comments = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}),
                               label='What needs correction')
