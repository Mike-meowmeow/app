"""Position catalog (HR-managed reusable vacancy defaults).

A position holds a default description and default requirements. They are
copied into a vacancy and its draft requirement version when the vacancy is
created (or when HR explicitly reloads defaults into a never-published draft).
After that the vacancy and the catalog are independent: editing one never
changes the other. Every catalog change is authorized and audited.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from accounts import roles
from audit import services as audit

from .models import (Position, PositionCompetency, PositionDocumentRequirement,
                     PositionQualificationStandard, QualificationStandard, VacancyCompetency,
                     VacancyDocumentRequirement)

POSITION_FIELDS = ('title', 'description')


def can_manage_catalog(user):
    return roles.has_role(user, roles.HR)


def _require_catalog(actor):
    if not can_manage_catalog(actor):
        raise PermissionDenied('Only HR Personnel can manage the position catalog.')


def _clean_title(title):
    title = ' '.join((title or '').split())
    if not title:
        raise ValidationError({'title': 'Enter a position title.'})
    return title


def _check_unique(title, exclude=None):
    qs = Position.objects.filter(title__iexact=title)
    if exclude is not None:
        qs = qs.exclude(pk=exclude.pk)
    if qs.exists():
        raise ValidationError({'title': 'A position with this title already exists.'})


def _locked(position):
    return Position.objects.select_for_update().get(pk=position.pk)


# --------------------------------------------------------------------------
# Positions
# --------------------------------------------------------------------------

@transaction.atomic
def create_position(actor, *, title, description):
    _require_catalog(actor)
    title = _clean_title(title)
    _check_unique(title)
    position = Position(title=title, description=description)
    position.full_clean()
    position.save()
    audit.record('position.created', entity=position, changes=audit.snapshot(position))
    return position


@transaction.atomic
def update_position(actor, position, *, reason='', **data):
    """Edit the catalog entry only. Existing vacancies keep their copies."""
    _require_catalog(actor)
    position = _locked(position)
    if 'title' in data:
        data['title'] = _clean_title(data['title'])
        _check_unique(data['title'], exclude=position)
    before = audit.snapshot(position)
    for field in POSITION_FIELDS:
        if field in data:
            setattr(position, field, data[field])
    position.full_clean()
    position.save()
    changes = audit.diff(before, audit.snapshot(position))
    if changes:
        audit.record('position.updated', entity=position, changes=changes, reason=reason)
    return position


@transaction.atomic
def set_position_active(actor, position, active, *, reason=''):
    """Deactivated positions cannot be chosen for new vacancies; vacancies
    already linked to them are unaffected."""
    _require_catalog(actor)
    position = _locked(position)
    if position.is_active == active:
        return position
    position.is_active = active
    position.save(update_fields=['is_active'])
    audit.record('position.reactivated' if active else 'position.deactivated',
                 entity=position, reason=reason,
                 changes={'is_active': {'old': not active, 'new': active}})
    return position


# --------------------------------------------------------------------------
# Default requirements
# --------------------------------------------------------------------------

@transaction.atomic
def add_document_default(actor, position, *, document_type, is_required=True, max_files=1,
                         instructions=''):
    _require_catalog(actor)
    position = _locked(position)
    if not document_type.is_active:
        raise ValidationError('This document type is inactive.')
    if position.document_requirements.filter(document_type=document_type).exists():
        raise ValidationError('This document type is already in the default checklist.')
    item = PositionDocumentRequirement(position=position, document_type=document_type,
                                       is_required=is_required, max_files=max_files,
                                       instructions=instructions)
    item.full_clean()
    item.save()
    audit.record('position.requirement_added', entity=position, changes={
        'document': document_type.code, 'is_required': is_required})
    return item


@transaction.atomic
def add_standard_default(actor, position, *, criterion_type, description, **values):
    from .services import build_standard
    _require_catalog(actor)
    position = _locked(position)
    operator, unit, required_value = build_standard(criterion_type, **values)
    item = PositionQualificationStandard.objects.create(
        position=position, criterion_type=criterion_type, operator=operator, unit=unit,
        required_value=required_value, description=description)
    audit.record('position.requirement_added', entity=position, changes={
        'standard': criterion_type, 'value': required_value})
    return item


@transaction.atomic
def add_competency_default(actor, position, *, name, description=''):
    _require_catalog(actor)
    position = _locked(position)
    item = PositionCompetency.objects.create(position=position, name=name,
                                             description=description)
    audit.record('position.requirement_added', entity=position, changes={'competency': name})
    return item


ITEM_MODELS = {
    'document': PositionDocumentRequirement,
    'standard': PositionQualificationStandard,
    'competency': PositionCompetency,
}


@transaction.atomic
def remove_default(actor, position, kind, item_id):
    _require_catalog(actor)
    position = _locked(position)
    try:
        item = ITEM_MODELS[kind].objects.get(pk=item_id, position=position)
    except ITEM_MODELS[kind].DoesNotExist:
        raise ValidationError('That default requirement no longer exists.')
    audit.record('position.requirement_removed', entity=position,
                 changes={'kind': kind, 'item': str(item_id), 'summary': _summary(kind, item)})
    item.delete()


def _summary(kind, item):
    if kind == 'document':
        return item.document_type.code
    if kind == 'standard':
        return {'criterion_type': item.criterion_type, 'value': item.required_value}
    return item.name


# --------------------------------------------------------------------------
# Copying defaults into a vacancy draft
# --------------------------------------------------------------------------

def defaults_manifest(position):
    """What a vacancy would receive from this position (also used for the
    audit record and the vacancy form preview)."""
    return {
        'position_id': str(position.pk),
        'title': position.title,
        'description': position.description,
        'documents': [{'document_type': d.document_type.code, 'name': d.document_type.name,
                       'is_required': d.is_required, 'max_files': d.max_files}
                      for d in position.document_requirements.select_related('document_type')],
        'standards': [{'criterion_type': s.criterion_type, 'operator': s.operator,
                       'required_value': s.required_value, 'unit': s.unit,
                       'description': s.description}
                      for s in position.qualification_standards.all()],
        'competencies': [{'name': c.name, 'description': c.description}
                         for c in position.competencies.all()],
    }


def copy_defaults(position, draft):
    """Create independent requirement rows in a draft version. Inactive
    document types are skipped so a draft never starts with a checklist item
    that would block publication; the skipped names are returned."""
    skipped = []
    for d in position.document_requirements.select_related('document_type'):
        if not d.document_type.is_active:
            skipped.append(d.document_type.name)
            continue
        VacancyDocumentRequirement.objects.create(
            requirement_version=draft, document_type=d.document_type,
            is_required=d.is_required, max_files=d.max_files, instructions=d.instructions)
    for s in position.qualification_standards.all():
        QualificationStandard.objects.create(
            requirement_version=draft, criterion_type=s.criterion_type, operator=s.operator,
            required_value=s.required_value, unit=s.unit, description=s.description)
    for c in position.competencies.all():
        VacancyCompetency.objects.create(requirement_version=draft, name=c.name,
                                         description=c.description)
    return skipped
