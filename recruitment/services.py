"""Vacancy configuration and lifecycle (FR-005, FR-026). Every operation checks
authorization, runs in a transaction, and records an audit event."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from accounts import roles
from audit import services as audit

from . import position_services, selectors
from .models import (AssignmentRole, CriterionType, Position, QualificationStandard,
                     StandardOperator, Vacancy, VacancyAssignment, VacancyCompetency,
                     VacancyDocumentRequirement, VacancyRequirementVersion, VacancyStatus)

MANIFEST_SCHEMA_VERSION = 1
EDITABLE_WHEN_PUBLISHED = ('description', 'positions_available', 'deadline_at')
VACANCY_FIELDS = ('department', 'recruitment_type', 'position_title', 'description',
                  'positions_available', 'opens_at', 'deadline_at')


def _require_manage(actor, vacancy):
    if not selectors.can_manage(actor, vacancy):
        raise PermissionDenied('You are not assigned to manage this vacancy.')


def _locked(vacancy):
    return Vacancy.objects.select_for_update().get(pk=vacancy.pk)


# --------------------------------------------------------------------------
# Vacancy records
# --------------------------------------------------------------------------

def _usable_position(position):
    if position is None:
        raise ValidationError({'position': 'Choose a position from the catalog.'})
    position = Position.objects.select_for_update().get(pk=position.pk)
    if not position.is_active:
        raise ValidationError({'position': 'This position is inactive and cannot be used '
                                           'for new vacancies.'})
    return position


def _record_defaults_applied(vacancy, position, draft, *, description_customized, skipped,
                             replaced=None):
    changes = {'position': str(position.pk), 'position_title': position.title,
               'requirement_version': draft.version,
               'description_customized': description_customized,
               'copied': position_services.defaults_manifest(position)}
    if skipped:
        changes['skipped_inactive_documents'] = skipped
    if replaced is not None:
        changes['replaced'] = replaced
    audit.record('vacancy.position_defaults_applied', entity=vacancy, changes=changes)


@transaction.atomic
def create_vacancy(actor, *, position, description=None, **data):
    """Create a draft vacancy from a catalog position. The position title,
    description (unless HR supplied an edited one) and default requirements
    are copied; the vacancy keeps those copies independently of the catalog."""
    if not roles.has_role(actor, roles.HR):
        raise PermissionDenied('Only HR Personnel can create vacancies.')
    position = _usable_position(position)
    fields = {f: data[f] for f in VACANCY_FIELDS if f in data
              and f not in ('position_title', 'description')}
    text = description if description and description.strip() else position.description
    vacancy = Vacancy(position=position, position_title=position.title, description=text,
                      **fields)
    vacancy.full_clean()
    vacancy.save()
    draft = VacancyRequirementVersion.objects.create(vacancy=vacancy, version=1)
    skipped = position_services.copy_defaults(position, draft)
    VacancyAssignment.objects.create(vacancy=vacancy, user=actor,
                                     assignment_role=AssignmentRole.HR, assigned_by=actor)
    audit.record('vacancy.created', entity=vacancy, changes=audit.snapshot(vacancy))
    _record_defaults_applied(vacancy, position, draft, skipped=skipped,
                             description_customized=text != position.description)
    return vacancy


def _draft_contents(draft):
    return {'documents': [d.document_type.code for d in
                          draft.document_requirements.select_related('document_type')],
            'standards': [s.description for s in draft.qualification_standards.all()],
            'competencies': [c.name for c in draft.competencies.all()]}


@transaction.atomic
def apply_position_defaults(actor, vacancy, position, *, confirm=False):
    """Explicitly (re)load a position's defaults into a never-published draft
    vacancy. Replaces the title, description, and draft requirements, so HR
    must confirm; the replaced values are kept in the audit record."""
    _require_manage(actor, vacancy)
    vacancy = _locked(vacancy)
    if vacancy.status != VacancyStatus.DRAFT or vacancy.posted_at or             vacancy.requirement_versions.filter(published_at__isnull=False).exists():
        raise ValidationError('Position defaults can only be loaded into a draft vacancy '
                              'that has never been published.')
    if not confirm:
        raise ValidationError('Confirm that the current title, description and draft '
                              'requirements will be replaced.')
    position = _usable_position(position)
    draft = _draft(vacancy)
    replaced = {'position': str(vacancy.position_id) if vacancy.position_id else None,
                'position_title': vacancy.position_title, 'description': vacancy.description,
                **_draft_contents(draft)}
    for model in (VacancyDocumentRequirement, QualificationStandard, VacancyCompetency):
        for item in model.objects.filter(requirement_version=draft):
            item.delete()
    vacancy.position = position
    vacancy.position_title = position.title
    vacancy.description = position.description
    vacancy.full_clean()
    vacancy.save(update_fields=['position', 'position_title', 'description'])
    skipped = position_services.copy_defaults(position, draft)
    _record_defaults_applied(vacancy, position, draft, skipped=skipped,
                             description_customized=False, replaced=replaced)
    return vacancy


@transaction.atomic
def update_vacancy(actor, vacancy, *, reason='', **data):
    _require_manage(actor, vacancy)
    vacancy = _locked(vacancy)
    if vacancy.status == VacancyStatus.DRAFT:
        allowed = VACANCY_FIELDS
    elif vacancy.status == VacancyStatus.PUBLISHED:
        allowed = EDITABLE_WHEN_PUBLISHED
        if not reason:
            raise ValidationError('A reason is required to change a published vacancy.')
        deadline = data.get('deadline_at', vacancy.deadline_at)
        if deadline and deadline <= timezone.now():
            raise ValidationError('The deadline of a published vacancy must be in the future.')
    else:
        raise ValidationError(f'A {vacancy.get_status_display().lower()} vacancy cannot be edited.')
    if vacancy.position_id:
        # The title comes from the catalog position; change it by loading
        # another position's defaults, not by editing the copy.
        allowed = tuple(f for f in allowed if f != 'position_title')

    before = audit.snapshot(vacancy)
    for field in allowed:
        if field in data:
            setattr(vacancy, field, data[field])
    vacancy.full_clean()
    vacancy.save()
    vacancy.refresh_from_db()
    changes = audit.diff(before, audit.snapshot(vacancy))
    if changes:
        audit.record('vacancy.updated', entity=vacancy, changes=changes, reason=reason)
    return vacancy


@transaction.atomic
def delete_draft_vacancy(actor, vacancy):
    """Only never-published drafts can be deleted; anything else is canceled."""
    _require_manage(actor, vacancy)
    vacancy = _locked(vacancy)
    if vacancy.status != VacancyStatus.DRAFT or vacancy.posted_at or \
            vacancy.requirement_versions.filter(published_at__isnull=False).exists():
        raise ValidationError('Only unpublished draft vacancies can be deleted. '
                              'Cancel published vacancies instead.')
    audit.record('vacancy.deleted', entity=vacancy, changes=audit.snapshot(vacancy))
    vacancy.assignments.all().delete()
    for version in vacancy.requirement_versions.all():
        version.delete()
    vacancy.delete()


# --------------------------------------------------------------------------
# Requirement versions
# --------------------------------------------------------------------------

def _draft(vacancy):
    draft = vacancy.draft_requirements()
    if draft is None:
        raise ValidationError('Start a requirements update before changing requirements.')
    return draft


@transaction.atomic
def start_requirements_update(actor, vacancy):
    """Create a new draft version, copied from the current published version."""
    _require_manage(actor, vacancy)
    vacancy = _locked(vacancy)
    if vacancy.status not in (VacancyStatus.DRAFT, VacancyStatus.PUBLISHED):
        raise ValidationError('Requirements can only change while the vacancy is draft or '
                              'published.')
    existing = vacancy.draft_requirements()
    if existing:
        return existing
    source = vacancy.current_requirements()
    next_version = (vacancy.requirement_versions.aggregate(m=Max('version'))['m'] or 0) + 1
    draft = VacancyRequirementVersion.objects.create(vacancy=vacancy, version=next_version)
    if source:
        for item in source.document_requirements.all():
            VacancyDocumentRequirement.objects.create(
                requirement_version=draft, document_type=item.document_type,
                is_required=item.is_required, max_files=item.max_files,
                instructions=item.instructions)
        for item in source.qualification_standards.all():
            QualificationStandard.objects.create(
                requirement_version=draft, criterion_type=item.criterion_type,
                operator=item.operator, required_value=item.required_value, unit=item.unit,
                description=item.description)
        for item in source.competencies.all():
            VacancyCompetency.objects.create(requirement_version=draft, name=item.name,
                                             description=item.description)
    audit.record('vacancy.requirements_draft_started', entity=vacancy,
                 changes={'version': next_version,
                          'copied_from': source.version if source else None})
    return draft


@transaction.atomic
def discard_requirements_draft(actor, vacancy):
    _require_manage(actor, vacancy)
    draft = _draft(vacancy)
    if not vacancy.current_requirements():
        raise ValidationError('A vacancy that was never published keeps its first draft.')
    audit.record('vacancy.requirements_draft_discarded', entity=vacancy,
                 changes={'version': draft.version})
    draft.delete()


@transaction.atomic
def add_document_requirement(actor, vacancy, **data):
    _require_manage(actor, vacancy)
    draft = _draft(vacancy)
    if draft.document_requirements.filter(document_type=data['document_type']).exists():
        raise ValidationError('This document type is already in the checklist.')
    item = VacancyDocumentRequirement.objects.create(requirement_version=draft, **data)
    audit.record('vacancy.requirement_added', entity=vacancy, changes={
        'version': draft.version, 'document': data['document_type'].code,
        'is_required': item.is_required})
    return item


STANDARD_RULES = {
    # criterion: (operator, unit, required value keys)
    CriterionType.EDUCATION: (StandardOperator.MINIMUM, '', ('degree_level',)),
    CriterionType.EXPERIENCE: (StandardOperator.MINIMUM, 'years', ('amount',)),
    CriterionType.TRAINING: (StandardOperator.MINIMUM, 'hours', ('amount',)),
    CriterionType.ELIGIBILITY: (StandardOperator.REQUIRED, '', ('name',)),
    CriterionType.LICENSE: (StandardOperator.REQUIRED, '', ('name',)),
    CriterionType.OTHER: (StandardOperator.SPECIFIC, '', ()),
}


def build_standard(criterion_type, *, degree_level='', amount=None, name='', field=''):
    """Derive operator, unit, and structured value for a criterion."""
    operator, unit, keys = STANDARD_RULES[criterion_type]
    value = {}
    if 'degree_level' in keys:
        value['degree_level'] = degree_level
    if 'amount' in keys:
        value['amount'] = str(amount) if amount is not None else None
    if 'name' in keys:
        value['name'] = name
    if field:
        value['field'] = field
    missing = [k for k in keys if not value.get(k)]
    if missing:
        raise ValidationError(f'Missing value for this criterion: {", ".join(missing)}.')
    return operator, unit, value


@transaction.atomic
def add_qualification_standard(actor, vacancy, *, criterion_type, description, **values):
    _require_manage(actor, vacancy)
    draft = _draft(vacancy)
    operator, unit, required_value = build_standard(criterion_type, **values)
    item = QualificationStandard.objects.create(
        requirement_version=draft, criterion_type=criterion_type, operator=operator,
        unit=unit, required_value=required_value, description=description)
    audit.record('vacancy.requirement_added', entity=vacancy, changes={
        'version': draft.version, 'standard': criterion_type, 'value': required_value})
    return item


@transaction.atomic
def add_competency(actor, vacancy, *, name, description=''):
    _require_manage(actor, vacancy)
    draft = _draft(vacancy)
    item = VacancyCompetency.objects.create(requirement_version=draft, name=name,
                                            description=description)
    audit.record('vacancy.requirement_added', entity=vacancy,
                 changes={'version': draft.version, 'competency': name})
    return item


ITEM_MODELS = {
    'document': VacancyDocumentRequirement,
    'standard': QualificationStandard,
    'competency': VacancyCompetency,
}


@transaction.atomic
def remove_requirement_item(actor, vacancy, kind, item_id):
    _require_manage(actor, vacancy)
    draft = _draft(vacancy)
    model = ITEM_MODELS[kind]
    item = model.objects.get(pk=item_id, requirement_version=draft)
    audit.record('vacancy.requirement_removed', entity=vacancy,
                 changes={'version': draft.version, 'kind': kind, 'item': str(item_id)})
    item.delete()


def publication_problems(vacancy, draft):
    """Human-readable list of missing configuration (FR-026). Empty = ready."""
    problems = []
    if not vacancy.department.is_active:
        problems.append('The department is inactive.')
    if not vacancy.recruitment_type.is_active:
        problems.append('The recruitment type is inactive.')
    if not vacancy.description.strip():
        problems.append('Add a description.')
    if not vacancy.opens_at:
        problems.append('Set the opening date.')
    if not vacancy.deadline_at:
        problems.append('Set the application deadline.')
    elif vacancy.deadline_at <= timezone.now():
        problems.append('The application deadline must be in the future.')
    if draft is None:
        problems.append('There are no draft requirements to publish.')
        return problems
    docs = list(draft.document_requirements.select_related('document_type'))
    if not any(d.is_required for d in docs):
        problems.append('Add at least one required document to the checklist.')
    inactive = [d.document_type.name for d in docs if not d.document_type.is_active]
    if inactive:
        problems.append(f'Inactive document types in the checklist: {", ".join(inactive)}.')
    if not draft.qualification_standards.exists():
        problems.append('Add at least one qualification standard.')
    return problems


def _manifests(draft):
    def wrap(items):
        return {'schema_version': MANIFEST_SCHEMA_VERSION, 'items': items}

    return {
        'document_checklist': wrap([
            {'document_type': d.document_type.code, 'name': d.document_type.name,
             'is_required': d.is_required, 'max_files': d.max_files,
             'instructions': d.instructions}
            for d in draft.document_requirements.select_related('document_type')]),
        'qualification_rules': wrap([
            {'criterion_type': s.criterion_type, 'operator': s.operator,
             'required_value': s.required_value, 'unit': s.unit,
             'description': s.description}
            for s in draft.qualification_standards.all()]),
        'competency_definitions': wrap([
            {'name': c.name, 'description': c.description}
            for c in draft.competencies.all()]),
    }


@transaction.atomic
def publish(actor, vacancy, *, reason=''):
    """Publish a draft vacancy, or publish updated requirements of a published
    vacancy (a reason is required then). Older versions remain unchanged."""
    _require_manage(actor, vacancy)
    vacancy = _locked(vacancy)
    if vacancy.status not in (VacancyStatus.DRAFT, VacancyStatus.PUBLISHED):
        raise ValidationError('Only draft or published vacancies can be published.')
    is_update = vacancy.status == VacancyStatus.PUBLISHED
    if is_update and not reason:
        raise ValidationError('A reason is required to publish changed requirements.')
    draft = vacancy.draft_requirements()
    problems = publication_problems(vacancy, draft)
    if problems:
        raise ValidationError(problems)

    now = timezone.now()
    for field, value in _manifests(draft).items():
        setattr(draft, field, value)
    draft.published_at = now
    draft.published_by = actor
    draft.save()
    if not is_update:
        vacancy.status = VacancyStatus.PUBLISHED
        vacancy.posted_at = now
        vacancy.save(update_fields=['status', 'posted_at'])
    audit.record('vacancy.requirements_published' if is_update else 'vacancy.published',
                 entity=vacancy, reason=reason,
                 changes={'version': draft.version, 'status': vacancy.status})
    if not is_update:
        from committee.services import create_committee
        create_committee(actor, vacancy, automatic=True)
    return draft


@transaction.atomic
def close_vacancy(actor, vacancy, *, reason):
    _require_manage(actor, vacancy)
    vacancy = _locked(vacancy)
    if vacancy.status != VacancyStatus.PUBLISHED:
        raise ValidationError('Only published vacancies can be closed.')
    vacancy.status = VacancyStatus.CLOSED
    vacancy.save(update_fields=['status'])
    audit.record('vacancy.closed', entity=vacancy, reason=reason,
                 changes={'status': {'old': 'published', 'new': 'closed'}})
    return vacancy


@transaction.atomic
def cancel_vacancy(actor, vacancy, *, reason):
    _require_manage(actor, vacancy)
    vacancy = _locked(vacancy)
    if vacancy.status not in (VacancyStatus.DRAFT, VacancyStatus.PUBLISHED,
                              VacancyStatus.CLOSED):
        raise ValidationError(f'A {vacancy.get_status_display().lower()} vacancy cannot be '
                              'canceled.')
    old = vacancy.status
    vacancy.status = VacancyStatus.CANCELED
    vacancy.canceled_at = timezone.now()
    vacancy.save(update_fields=['status', 'canceled_at'])
    audit.record('vacancy.canceled', entity=vacancy, reason=reason,
                 changes={'status': {'old': old, 'new': 'canceled'}})
    return vacancy


# --------------------------------------------------------------------------
# Staff assignments
# --------------------------------------------------------------------------

ROLE_FOR_ASSIGNMENT = {AssignmentRole.HR: roles.HR,
                       AssignmentRole.DEPARTMENT_HEAD: roles.DEPARTMENT_HEAD}


@transaction.atomic
def assign_staff(actor, vacancy, user, assignment_role, *, reason=''):
    if not selectors.can_assign_staff(actor, vacancy):
        raise PermissionDenied('You cannot assign staff to this vacancy.')
    needed = ROLE_FOR_ASSIGNMENT[assignment_role]
    if not user.is_active or not roles.has_role(user, needed):
        raise ValidationError(f'{user.username} must be an active {needed}.')
    assignment, created = VacancyAssignment.objects.select_for_update().get_or_create(
        vacancy=vacancy, user=user, assignment_role=assignment_role,
        defaults={'assigned_by': actor})
    if not created:
        if assignment.ended_at is None:
            raise ValidationError(f'{user.username} is already assigned in this role.')
        assignment.ended_at = None
        assignment.assigned_by = actor
        assignment.save(update_fields=['ended_at', 'assigned_by'])
    audit.record('vacancy.assignment_added' if created else 'vacancy.assignment_reactivated',
                 entity=vacancy, reason=reason,
                 changes={'user': user.username, 'assignment_role': assignment_role})
    return assignment


@transaction.atomic
def end_assignment(actor, assignment, *, reason=''):
    vacancy = assignment.vacancy
    if not selectors.can_assign_staff(actor, vacancy):
        raise PermissionDenied('You cannot change staff assignments for this vacancy.')
    if assignment.ended_at:
        return assignment
    if assignment.assignment_role == AssignmentRole.HR and not vacancy.assignments.filter(
            assignment_role=AssignmentRole.HR, ended_at__isnull=True).exclude(
            pk=assignment.pk).exists():
        raise ValidationError('A vacancy must keep at least one active HR handler.')
    assignment.ended_at = timezone.now()
    assignment.save(update_fields=['ended_at'])
    audit.record('vacancy.assignment_ended', entity=vacancy, reason=reason,
                 changes={'user': assignment.user.username,
                          'assignment_role': assignment.assignment_role})
    return assignment
