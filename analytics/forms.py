from django import forms

from core.forms import BootstrapFormMixin
from core.models import Department

from .models import ReportFormat, ReportType


class ScopeForm(BootstrapFormMixin, forms.Form):
    start = forms.DateField(required=False, label='Submitted from',
                            widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='Submitted to (inclusive)',
                          widget=forms.DateInput(attrs={'type': 'date'}))
    department = forms.ModelChoiceField(queryset=Department.objects.none(), required=False,
                                        empty_label='All permitted departments')
    vacancy = forms.ModelChoiceField(queryset=None, required=False,
                                     empty_label='All permitted vacancies')

    def __init__(self, *args, vacancies, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['vacancy'].queryset = vacancies.order_by('position_title')
        self.fields['department'].queryset = Department.objects.filter(
            pk__in=vacancies.values('department_id'))

    def clean(self):
        data = super().clean()
        if data.get('start') and data.get('end') and data['start'] > data['end']:
            raise forms.ValidationError('The start date must be on or before the end date.')
        return data


class ReportForm(BootstrapFormMixin, forms.Form):
    report_type = forms.ChoiceField(choices=ReportType.choices, label='Report')
    fmt = forms.ChoiceField(choices=ReportFormat.choices, label='Format')
