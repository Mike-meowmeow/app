from django import forms

from core.forms import BootstrapFormMixin

from .models import AttendanceStatus, ConflictStatus, InterviewMode, MemberRole

FORMAT = '%Y-%m-%dT%H:%M'
DATETIME_WIDGET = forms.DateTimeInput(attrs={'type': 'datetime-local'}, format=FORMAT)


def _datetime(label, **kwargs):
    return forms.DateTimeField(label=label, widget=DATETIME_WIDGET, input_formats=[FORMAT],
                               **kwargs)


def _text(label, rows=3, required=True, **kwargs):
    return forms.CharField(label=label, required=required,
                           widget=forms.Textarea(attrs={'rows': rows}), **kwargs)


class CommitteeForm(BootstrapFormMixin, forms.Form):
    name = forms.CharField(max_length=200, label='Committee name',
                           help_text='For example: Personnel Selection Board - College of '
                                     'Education')


class MemberForm(BootstrapFormMixin, forms.Form):
    user = forms.ModelChoiceField(queryset=None, label='Member',
                                  help_text='Active PSB Members and Department Heads.')
    member_role = forms.ChoiceField(choices=MemberRole.choices, label='Role on the committee')

    def __init__(self, *args, users=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['user'].queryset = users
        self.fields['user'].label_from_instance = \
            lambda u: f'{u.full_name or u.username} ({u.username})'


class ReasonForm(BootstrapFormMixin, forms.Form):
    reason = _text('Reason', rows=2)


class ConflictForm(BootstrapFormMixin, forms.Form):
    declaration = _text('Conflict of interest',
                        help_text='Describe the relationship or interest (for example: '
                                  'relative, former student, business partner).')


class ResolveConflictForm(BootstrapFormMixin, forms.Form):
    status = forms.ChoiceField(
        choices=[(ConflictStatus.RECUSED, 'Recused - the member does not take part'),
                 (ConflictStatus.CLEARED, 'Cleared - the member may take part')],
        widget=forms.RadioSelect, label='Resolution')
    resolution = _text('Reason / basis', rows=2)


class ScheduleFields(forms.Form):
    starts_at = _datetime('Starts (Asia/Manila)')
    ends_at = _datetime('Ends (Asia/Manila)')
    mode = forms.ChoiceField(choices=InterviewMode.choices, widget=forms.RadioSelect)
    location = _text('Venue', rows=2, required=False,
                     help_text='Required for in-person interviews.')
    meeting_link = forms.CharField(max_length=500, required=False, label='Meeting link',
                                   help_text='Required for online interviews.')


class InterviewForm(BootstrapFormMixin, ScheduleFields):
    panel = forms.ModelMultipleChoiceField(queryset=None, widget=forms.CheckboxSelectMultiple,
                                           label='Panel',
                                           help_text='Members with a conflict of interest for '
                                                     'this applicant are not listed.')

    def __init__(self, *args, members=(), **kwargs):
        super().__init__(*args, **kwargs)
        from .models import CommitteeMember
        self.fields['panel'].queryset = CommitteeMember.objects.filter(
            pk__in=[m.pk for m in members]).select_related('user', 'committee')
        self.fields['panel'].label_from_instance = \
            lambda m: f'{m.user.full_name or m.user.username} - {m.get_member_role_display()}, ' \
                      f'{m.committee.name}'


class RescheduleForm(BootstrapFormMixin, ScheduleFields):
    reason = _text('Reason for rescheduling', rows=2)

    @classmethod
    def for_interview(cls, interview):
        return cls(initial={'starts_at': interview.starts_at, 'ends_at': interview.ends_at,
                            'mode': interview.mode, 'location': interview.location,
                            'meeting_link': interview.meeting_link})


class AttendanceForm(BootstrapFormMixin, forms.Form):
    attendance = forms.ChoiceField(
        choices=[(AttendanceStatus.ATTENDED, 'Attended'),
                 (AttendanceStatus.NO_SHOW, 'No show')],
        widget=forms.RadioSelect)
    notes = _text('Notes', rows=2, required=False)


class EvaluationForm(BootstrapFormMixin, forms.Form):
    interview = forms.ChoiceField(required=False, label='Assessment of',
                                  help_text='An interview you sat on, or the application '
                                            'documents only.')
    findings = _text('Findings', rows=6,
                     help_text='Narrative findings. No score is required.')
    recommendation = _text('Recommendation', rows=3, required=False)

    def __init__(self, *args, interviews=None, **kwargs):
        super().__init__(*args, **kwargs)
        if interviews is None:
            del self.fields['interview']
        else:
            self.fields['interview'].choices = [('', 'Application documents only')] + [
                (str(i.pk), f'Interview on {i.starts_at:%b %d, %Y %I:%M %p}') for i in interviews]


class AmendEvaluationForm(BootstrapFormMixin, forms.Form):
    findings = _text('Findings', rows=6)
    recommendation = _text('Recommendation', rows=3, required=False)
    reason = _text('Reason for the amendment', rows=2,
                   help_text='The submitted version is kept unchanged.')
