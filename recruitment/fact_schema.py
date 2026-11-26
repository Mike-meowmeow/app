"""Field definitions for application fact values (schema version 1).

Each fact value is a JSON object with these fields plus `source_record_id`
(the profile record it came from) or `manual: true` for HR-entered facts.
"""

from django import forms

from applicants.models import CivilStatus, CompletionStatus, DegreeLevel, Sex

SCHEMA_VERSION = 1

# fact_type -> [(field, label, kind, choices)]; kind: text | long | date | decimal | choice
FACT_FIELDS = {
    'identity': [
        ('last_name', 'Last name', 'text', None),
        ('first_name', 'First name', 'text', None),
        ('middle_name', 'Middle name', 'text', None),
        ('email', 'Email', 'text', None),
        ('contact_no', 'Contact number', 'text', None),
        ('birthdate', 'Birthdate', 'date', None),
        ('sex', 'Sex', 'choice', Sex.choices),
        ('address', 'Address', 'long', None),
        ('civil_status', 'Civil status', 'choice', CivilStatus.choices),
    ],
    'education': [
        ('degree_level', 'Degree level', 'choice', DegreeLevel.choices),
        ('program', 'Degree / program', 'text', None),
        ('institution', 'Institution', 'text', None),
        ('completion_status', 'Completion status', 'choice', CompletionStatus.choices),
        ('completed_on', 'Date completed', 'date', None),
    ],
    'experience': [
        ('position', 'Position', 'text', None),
        ('organization', 'Employer / organization', 'text', None),
        ('start_date', 'Start date', 'date', None),
        ('end_date', 'End date', 'date', None),
        ('duties', 'Duties', 'long', None),
    ],
    'training': [
        ('title', 'Title', 'text', None),
        ('provider', 'Conducted by', 'text', None),
        ('hours', 'Hours', 'decimal', None),
        ('start_date', 'Start date', 'date', None),
        ('end_date', 'End date', 'date', None),
    ],
    'eligibility': [
        ('eligibility_type', 'Eligibility', 'text', None),
        ('rating', 'Rating', 'decimal', None),
        ('date_obtained', 'Date obtained', 'date', None),
    ],
    'license': [
        ('profession', 'Profession', 'text', None),
        ('license_number', 'License number', 'text', None),
        ('issuing_body', 'Issuing body', 'text', None),
        ('issued_on', 'Issued on', 'date', None),
        ('expires_on', 'Expires on', 'date', None),
    ],
}

REQUIRED_FIELDS = {
    'identity': ('last_name', 'first_name'),
    'education': ('degree_level', 'program', 'institution', 'completion_status'),
    'experience': ('position', 'organization', 'start_date'),
    'training': ('title',),
    'eligibility': ('eligibility_type',),
    'license': ('profession', 'license_number'),
}

# Profile record kinds captured in snapshots, in display order.
PROFILE_KINDS = ('education', 'experience', 'training', 'eligibility', 'license')

# Facts whose relevance HR confirms (FR-009).
RELEVANCE_TYPES = ('experience', 'training')

QUALIFICATION_OUTCOMES = [
    ('meets', 'Meets Requirement'),
    ('does_not_meet', 'Does Not Meet Requirement'),
    ('needs_review', 'Needs Review'),
    ('not_required', 'Not Required'),
]
OUTCOME_LABELS = dict(QUALIFICATION_OUTCOMES)


def display_items(fact_type, value):
    """[(label, value)] for templates, skipping bookkeeping keys."""
    labels = {name: (label, choices) for name, label, _, choices in FACT_FIELDS.get(fact_type, [])}
    items = []
    for name, (label, choices) in labels.items():
        raw = value.get(name)
        if raw in (None, ''):
            continue
        if choices:
            raw = dict(choices).get(raw, raw)
        items.append((label, raw))
    return items


# Value fields that suggest names from the license/eligibility reference list.
CREDENTIAL_FIELDS = {'eligibility_type': 'eligibility', 'profession': 'license'}


def value_form_class(fact_type):
    """A form with one field per value field of the fact type."""
    attrs = {}
    required = REQUIRED_FIELDS.get(fact_type, ())
    for name, label, kind, choices in FACT_FIELDS[fact_type]:
        is_required = name in required
        if kind == 'choice':
            field = forms.ChoiceField(label=label, required=is_required,
                                      choices=[('', '---------')] + list(choices))
        elif kind == 'date':
            field = forms.DateField(label=label, required=is_required,
                                    widget=forms.DateInput(attrs={'type': 'date'},
                                                           format='%Y-%m-%d'))
        elif kind == 'decimal':
            field = forms.DecimalField(label=label, required=is_required, min_value=0,
                                       max_digits=9, decimal_places=2)
        elif kind == 'long':
            field = forms.CharField(label=label, required=is_required,
                                    widget=forms.Textarea(attrs={'rows': 2}))
        elif name in CREDENTIAL_FIELDS:
            from core.credentials import CredentialNameInput
            field = forms.CharField(label=label, required=is_required, max_length=255,
                                    widget=CredentialNameInput(CREDENTIAL_FIELDS[name]))
        else:
            field = forms.CharField(label=label, required=is_required, max_length=255)
        attrs[name] = field
    return type(f'{fact_type.title()}ValueForm', (forms.Form,), attrs)


def to_value(fact_type, cleaned):
    """JSON-safe value dict from cleaned form data."""
    value = {}
    for name, _, kind, _ in FACT_FIELDS[fact_type]:
        raw = cleaned.get(name)
        if raw in (None, ''):
            value[name] = None
        elif kind == 'date':
            value[name] = raw.isoformat()
        elif kind == 'decimal':
            value[name] = str(raw)
        else:
            value[name] = raw
    return value


def initial_from_value(fact_type, value):
    return {name: value.get(name) for name, _, _, _ in FACT_FIELDS[fact_type]}


# Keys that record where a fact came from rather than what it says: the profile
# record, an HR entry, or (Stage 10) the AI extraction item HR accepted.
BOOKKEEPING_KEYS = ('source_record_id', 'manual', 'extraction_item_id')


def comparable(value):
    """Value without bookkeeping keys, for change detection."""
    return {k: v for k, v in value.items() if k not in BOOKKEEPING_KEYS}


def is_reviewer_entered(value):
    """Facts HR added (manually or from an AI extraction), which snapshot
    synchronization must never supersede."""
    return bool(value.get('manual') or value.get('extraction_item_id'))
