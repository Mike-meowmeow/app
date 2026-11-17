"""Official roster generation, review, approval, release, and export
(FR-021, FR-022, FR-023).

Membership: applications of the vacancy whose first submission time is not
null and not after the cutoff. Current status, verification, qualification
results, and AI results never affect membership.

Concurrency: generation, submission for approval, and approval lock the
vacancy row first, the same lock the submission service takes, and recheck
membership and application revisions against the roster's manifest. Any
roster-visible application change marks affected rosters as requiring
regeneration (see mark_outdated) without touching approved content.
"""

import hashlib
import shutil
import uuid
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts import roles
from audit import services as audit
from notifications import events
from recruitment import selectors as vacancy_access
from recruitment.models import Application, Vacancy

from . import builder, exporters, layout
from .models import (ApplicantRoster, ExportFormat, RosterApprovalEvent, RosterEntry,
                     RosterExport, RosterStatus, RosterTemplate, TemplateStatus)

R = RosterStatus
ACTIVE = (R.DRAFT, R.PENDING_APPROVAL, R.RETURNED)
EDITABLE = (R.DRAFT, R.RETURNED)
OUTDATABLE = ACTIVE + (R.APPROVED,)
TEMPLATE_MANAGERS = (roles.APPROVER, roles.SYSTEM_ADMIN)


class ExportFailed(Exception):
    pass


# --------------------------------------------------------------------------
# Access
# --------------------------------------------------------------------------

def can_prepare(user, vacancy):
    """HR with an active HR assignment on the vacancy."""
    return vacancy_access.can_manage(user, vacancy)


def can_approve(user):
    return roles.has_role(user, roles.APPROVER)


def can_view(user, vacancy):
    return can_prepare(user, vacancy) or can_approve(user)


def _require_prepare(user, vacancy):
    if not can_prepare(user, vacancy):
        raise PermissionDenied('You are not assigned to prepare rosters for this vacancy.')


def _require_approver(user):
    if not can_approve(user):
        raise PermissionDenied('Only a designated approver can do this.')


def self_approval_allowed():
    return getattr(settings, 'ROSTER_ALLOW_SELF_APPROVAL', False)


# --------------------------------------------------------------------------
# Templates
# --------------------------------------------------------------------------

def template_root():
    return Path(settings.PRIVATE_STORAGE_ROOT) / 'templates'


def export_root():
    return Path(settings.EXPORT_STORAGE_ROOT)


def _safe_path(base, key):
    base = base.resolve()
    path = (base / key).resolve()
    if base not in path.parents:
        raise ValidationError('Invalid storage key.')
    return path


def template_path(template):
    """Path of a template file after checking its integrity."""
    path = _safe_path(template_root(), template.storage_key)
    if not path.exists():
        raise ValidationError(f'The roster template file for "{template.name}" is missing. '
                              'A System Administrator must register it again.')
    if _sha256_file(path) != template.sha256:
        raise ValidationError(f'The roster template file for "{template.name}" was changed '
                              'outside the system and cannot be used.')
    return path


def _sha256_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(65536), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _signatories(prepared_name='', prepared_position='', noted_name='', noted_position=''):
    return {'schema_version': 1,
            'prepared_by': {'name': prepared_name.strip(), 'position': prepared_position.strip()},
            'noted_by': {'name': noted_name.strip(), 'position': noted_position.strip()}}


def layout_allowed(recruitment_type):
    return recruitment_type.code in getattr(settings, 'ROSTER_PART_TIME_TYPES', ('PTI',))


def register_template(source_path, recruitment_type, *, name, signatories=None, actor=None):
    """Validate and store a template workbook as a new Draft version."""
    from openpyxl import load_workbook
    if not layout_allowed(recruitment_type):
        raise ValidationError(
            f'The PART-TIME roster form is approved only for part-time instructor recruitment. '
            f'"{recruitment_type.name}" needs its own HR-approved template.')
    source_path = Path(source_path)
    try:
        workbook = load_workbook(source_path)
    except Exception as exc:
        raise ValidationError(f'The file could not be opened as an Excel workbook: {exc}')
    try:
        layout.validate(workbook)
    except ValueError as exc:
        raise ValidationError(str(exc))
    key = f'{uuid.uuid4().hex}.xlsx'
    target = _safe_path(template_root(), key)
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, target)
    try:
        with transaction.atomic():
            version = (RosterTemplate.objects.filter(recruitment_type=recruitment_type)
                       .order_by('-version').values_list('version', flat=True).first() or 0) + 1
            template = RosterTemplate.objects.create(
                recruitment_type=recruitment_type, version=version, name=name,
                storage_key=key, sha256=_sha256_file(target),
                signatory_defaults=signatories or _signatories())
            audit.record('roster_template.registered', entity=template, actor=actor,
                         changes={'version': version, 'sha256': template.sha256,
                                  'recruitment_type': recruitment_type.code})
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return template


@transaction.atomic
def approve_template(actor, template):
    if not roles.has_role(actor, *TEMPLATE_MANAGERS):
        raise PermissionDenied('Only an approver or System Administrator can approve templates.')
    template = RosterTemplate.objects.select_for_update().get(pk=template.pk)
    if template.status != TemplateStatus.DRAFT:
        raise ValidationError('Only draft templates can be approved.')
    template_path(template)
    for old in RosterTemplate.objects.select_for_update().filter(
            recruitment_type=template.recruitment_type, status=TemplateStatus.APPROVED):
        old.status = TemplateStatus.RETIRED
        old.save(update_fields=['status'])
        audit.record('roster_template.retired', entity=old, actor=actor,
                     changes={'replaced_by_version': template.version})
    template.status = TemplateStatus.APPROVED
    template.approved_by = actor
    template.approved_at = timezone.now()
    template.save(update_fields=['status', 'approved_by', 'approved_at'])
    audit.record('roster_template.approved', entity=template, actor=actor,
                 changes={'version': template.version})
    return template


@transaction.atomic
def update_template_signatories(actor, template, signatories):
    """Change default signatories for future rosters. Existing rosters keep
    their own signatory snapshot, so past exports never change."""
    if not roles.has_role(actor, *TEMPLATE_MANAGERS):
        raise PermissionDenied('Only an approver or System Administrator can change signatories.')
    template = RosterTemplate.objects.select_for_update().get(pk=template.pk)
    old = template.signatory_defaults
    template.signatory_defaults = signatories
    template.save(update_fields=['signatory_defaults'])
    audit.record('roster_template.signatories_changed', entity=template, actor=actor,
                 changes={'old': old, 'new': signatories})
    return template


def approved_template_for(recruitment_type):
    return RosterTemplate.objects.filter(recruitment_type=recruitment_type,
                                         status=TemplateStatus.APPROVED) \
        .order_by('-version').first()


def configuration_problems(vacancy):
    """Missing configuration that blocks roster preparation (FR-026)."""
    if approved_template_for(vacancy.recruitment_type) is None:
        return [f'No approved roster template exists for {vacancy.recruitment_type.name}. '
                'A System Administrator must register one (manage.py register_roster_template).']
    return []


# --------------------------------------------------------------------------
# Membership and entries
# --------------------------------------------------------------------------

def members(vacancy, cutoff_at):
    return Application.objects.filter(vacancy=vacancy, submitted_at__isnull=False,
                                      submitted_at__lte=cutoff_at)


def current_manifest(vacancy, cutoff_at):
    return {str(pk): revision for pk, revision in
            members(vacancy, cutoff_at).values_list('pk', 'revision')}


def outdated_reason(roster):
    """Why the roster no longer matches its sources, or ''."""
    stored = roster.source_revision_manifest.get('applications', {})
    current = current_manifest(roster.vacancy, roster.cutoff_at)
    added = set(current) - set(stored)
    removed = set(stored) - set(current)
    changed = [pk for pk in set(current) & set(stored) if current[pk] != stored[pk]]
    parts = []
    if added:
        parts.append(f'{len(added)} new submission(s)')
    if removed:
        parts.append(f'{len(removed)} application(s) no longer within the cutoff')
    if changed:
        parts.append(f'{len(changed)} application(s) changed since generation')
    return ', '.join(parts)


def _header(template, vacancy, cutoff_at):
    from openpyxl import load_workbook
    header = layout.read_header(load_workbook(template_path(template)))
    header.update({
        'schema_version': 1,
        'layout': layout.LAYOUT,
        'department': vacancy.department.name.upper(),
        'vacancy': {'id': str(vacancy.pk), 'reference': vacancy.reference,
                    'position_title': vacancy.position_title},
        'template': {'id': str(template.pk), 'version': template.version,
                     'sha256': template.sha256},
        'cutoff_display': timezone.localtime(cutoff_at).strftime('%B %d, %Y %I:%M %p'),
    })
    return header


def _without_number(values):
    return {k: v for k, v in values.items() if k != 'A'}


def _flags(field_flags, document_flag, previous, exported):
    """Review flags for a new entry. An acknowledgment carries over only when
    the row's values and flags are unchanged from the previous version."""
    flags = {'schema_version': 1, 'fields': field_flags}
    if document_flag:
        flags['documents'] = document_flag
    if previous:
        old = previous.review_flags
        same = (old.get('fields') == field_flags and old.get('documents') == document_flag
                and _without_number(previous.exported_values) == _without_number(exported))
        if same and old.get('acknowledged'):
            flags['acknowledged'] = old['acknowledged']
        if old.get('remarks_edited'):
            flags['remarks_edited'] = True
    return flags


def _populate(roster, previous_entries):
    """Build entries for all members. previous_entries: {application_id: entry}
    from the version being regenerated or replaced (keeps HR remarks)."""
    applications = list(members(roster.vacancy, roster.cutoff_at)
                        .select_related('applicant__user'))
    built = []
    for application in applications:
        values, field_flags, document_flag = builder.build_row(application)
        built.append((builder.sort_key(application, values), application, values,
                      field_flags, document_flag))
    built.sort(key=lambda item: item[0])
    manifest = {}
    for sequence, (_, application, values, field_flags, document_flag) in enumerate(built, 1):
        snapshot = application.latest_snapshot()
        previous = previous_entries.get(application.pk)
        if previous and previous.review_flags.get('remarks_edited'):
            remarks = previous.remarks
        else:
            remarks = builder.default_remarks(application, document_flag)
        exported = {'A': str(sequence), **values, 'T': remarks}
        RosterEntry.objects.create(
            roster=roster, application=application, source_snapshot=snapshot,
            sequence_number=sequence, exported_values=exported, remarks=remarks,
            review_flags=_flags(field_flags, document_flag, previous, exported))
        manifest[str(application.pk)] = application.revision
    roster.source_revision_manifest = {'schema_version': 1,
                                       'cutoff_at': roster.cutoff_at.isoformat(),
                                       'generated_at': roster.generated_at.isoformat(),
                                       'applications': manifest}
    roster.save(update_fields=['source_revision_manifest', 'generated_at'])


def _event(roster, from_status, to_status, actor, comments=''):
    return RosterApprovalEvent.objects.create(roster=roster, from_status=from_status or '',
                                       to_status=to_status, actor=actor, comments=comments)


def _lock_vacancy(vacancy):
    return Vacancy.objects.select_for_update().select_related(
        'department', 'recruitment_type').get(pk=vacancy.pk)


def _lock_roster(roster):
    return ApplicantRoster.objects.select_for_update().select_related(
        'vacancy__department', 'vacancy__recruitment_type', 'template').get(pk=roster.pk)


def default_cutoff(vacancy):
    return vacancy.deadline_at or timezone.now()


@transaction.atomic
def generate(actor, vacancy, *, cutoff_at=None):
    """Create the next roster version as a Draft."""
    vacancy = _lock_vacancy(vacancy)
    _require_prepare(actor, vacancy)
    active = vacancy.rosters.filter(status__in=ACTIVE).first()
    if active:
        raise ValidationError(f'Roster version {active.version} is still in preparation '
                              f'({active.get_status_display()}). Regenerate that version '
                              'instead of starting another.')
    problems = configuration_problems(vacancy)
    if problems:
        raise ValidationError(problems)
    template = approved_template_for(vacancy.recruitment_type)
    cutoff_at = cutoff_at or default_cutoff(vacancy)
    latest = vacancy.rosters.order_by('-version').first()
    roster = ApplicantRoster.objects.create(
        vacancy=vacancy, template=template, version=(latest.version + 1) if latest else 1,
        cutoff_at=cutoff_at, prepared_by=actor, generated_at=timezone.now(),
        header_snapshot=_header(template, vacancy, cutoff_at),
        signatory_snapshot=(latest.signatory_snapshot if latest and latest.template_id ==
                            template.pk else template.signatory_defaults) or _signatories())
    previous = {e.application_id: e for e in latest.entries.all()} if latest else {}
    _populate(roster, previous)
    _event(roster, '', R.DRAFT, actor, 'Generated')
    audit.record('roster.generated', entity=roster, actor=actor, changes={
        'vacancy': vacancy.reference, 'version': roster.version,
        'cutoff_at': cutoff_at.isoformat(), 'template_version': template.version,
        'entries': len(roster.source_revision_manifest['applications'])})
    return roster


@transaction.atomic
def regenerate(actor, roster):
    """Rebuild a draft or returned version from current application data,
    keeping HR remarks and acknowledgments for unchanged rows."""
    _lock_vacancy(roster.vacancy)
    roster = _lock_roster(roster)
    _require_prepare(actor, roster.vacancy)
    if roster.status not in EDITABLE:
        raise ValidationError('Only draft or returned roster versions can be regenerated.')
    template = approved_template_for(roster.vacancy.recruitment_type)
    if template is None:
        raise ValidationError(configuration_problems(roster.vacancy))
    previous = {e.application_id: e for e in roster.entries.all()}
    roster.entries.all().delete()
    if template.pk != roster.template_id:
        roster.template = template
        roster.save(update_fields=['template'])
    roster.header_snapshot = _header(template, roster.vacancy, roster.cutoff_at)
    roster.generated_at = timezone.now()
    roster.requires_regeneration = False
    roster.save(update_fields=['header_snapshot', 'generated_at', 'requires_regeneration'])
    _populate(roster, previous)
    audit.record('roster.regenerated', entity=roster, actor=actor, changes={
        'entries': len(roster.source_revision_manifest['applications'])})
    return roster


# --------------------------------------------------------------------------
# HR review of a version
# --------------------------------------------------------------------------

@transaction.atomic
def update_entry(actor, entry, *, remarks=None, acknowledge=False):
    roster = _lock_roster(entry.roster)
    _require_prepare(actor, roster.vacancy)
    if roster.status not in EDITABLE:
        raise ValidationError('This roster version can no longer be edited.')
    entry = RosterEntry.objects.select_for_update().get(pk=entry.pk)
    before = {'remarks': entry.remarks, 'acknowledged': bool(entry.review_flags.get('acknowledged'))}
    flags = dict(entry.review_flags)
    if remarks is not None and remarks.strip() != entry.remarks:
        entry.remarks = remarks.strip()
        entry.exported_values = {**entry.exported_values, 'T': entry.remarks}
        flags['remarks_edited'] = True
    if acknowledge and not flags.get('acknowledged') and entry.needs_acknowledgment:
        flags['acknowledged'] = {'by': str(actor.pk), 'name': actor.get_full_name() or actor.username,
                                 'at': timezone.now().isoformat()}
    entry.review_flags = flags
    entry.save(update_fields=['remarks', 'exported_values', 'review_flags'])
    after = {'remarks': entry.remarks, 'acknowledged': bool(flags.get('acknowledged'))}
    changes = audit.diff(before, after)
    if changes:
        audit.record('roster.entry_updated', entity=entry, actor=actor,
                     changes={**changes, 'roster_version': roster.version,
                              'application': entry.application.reference})
    return entry


@transaction.atomic
def update_signatories(actor, roster, signatories):
    roster = _lock_roster(roster)
    _require_prepare(actor, roster.vacancy)
    if roster.status not in EDITABLE:
        raise ValidationError('Signatories can only be changed while the version is editable.')
    old = roster.signatory_snapshot
    roster.signatory_snapshot = signatories
    roster.save(update_fields=['signatory_snapshot'])
    audit.record('roster.signatories_changed', entity=roster, actor=actor,
                 changes={'old': old, 'new': signatories})
    return roster


def approval_problems(roster):
    """What blocks sending this version for approval (or approving it)."""
    problems = []
    reason = outdated_reason(roster)
    if reason:
        problems.append(f'This version is out of date ({reason}). Regenerate it first.')
    elif roster.requires_regeneration:
        problems.append('Application information changed after this version was generated. '
                        'Regenerate it first.')
    unresolved = sum(1 for e in roster.entries.only('review_flags') if e.needs_acknowledgment)
    if unresolved:
        problems.append(f'{unresolved} row(s) have unresolved flags. Verify the information '
                        'or acknowledge each flagged row.')
    signatories = roster.signatory_snapshot or {}
    for key, label in (('prepared_by', 'Prepared by'), ('noted_by', 'Noted by')):
        person = signatories.get(key) or {}
        if not (person.get('name') and person.get('position')):
            problems.append(f'Enter the "{label}" signatory name and position.')
    return problems


def _flag_outdated(roster, actor, reason):
    if not roster.requires_regeneration:
        roster.requires_regeneration = True
        roster.save(update_fields=['requires_regeneration'])
        audit.record('roster.regeneration_required', entity=roster, actor=actor,
                     changes={'reason': reason})


def submit_for_approval(actor, roster):
    """Returns a list of problems (empty on success). A detected change is
    saved as requires_regeneration even though the submission is refused."""
    with transaction.atomic():
        _lock_vacancy(roster.vacancy)
        roster = _lock_roster(roster)
        _require_prepare(actor, roster.vacancy)
        if roster.status not in EDITABLE:
            return ['Only draft or returned versions can be sent for approval.']
        reason = outdated_reason(roster)
        if reason:
            _flag_outdated(roster, actor, reason)
        problems = approval_problems(roster)
        if problems:
            return problems
        from_status = roster.status
        roster.status = R.PENDING_APPROVAL
        roster.save(update_fields=['status'])
        events.roster_event(roster, _event(roster, from_status, R.PENDING_APPROVAL, actor))
        audit.record('roster.submitted_for_approval', entity=roster, actor=actor,
                     changes={'version': roster.version})
    return []


def approve(actor, roster, *, comments=''):
    """Approve a pending version. Rechecks membership and revisions under the
    vacancy lock shared with submission. Returns a list of problems."""
    _require_approver(actor)
    with transaction.atomic():
        _lock_vacancy(roster.vacancy)
        roster = _lock_roster(roster)
        if roster.status != R.PENDING_APPROVAL:
            return ['Only versions pending approval can be approved.']
        if roster.prepared_by_id == actor.pk and not self_approval_allowed():
            return ['You prepared this version; another designated approver must approve it.']
        reason = outdated_reason(roster)
        if reason:
            _flag_outdated(roster, actor, reason)
        problems = approval_problems(roster)
        if problems:
            return problems
        now = timezone.now()
        for old in roster.vacancy.rosters.select_for_update().filter(status=R.APPROVED) \
                .exclude(pk=roster.pk):
            old.status = R.SUPERSEDED
            old.save(update_fields=['status'])
            _event(old, R.APPROVED, R.SUPERSEDED, actor, f'Replaced by version {roster.version}')
            audit.record('roster.superseded', entity=old, actor=actor,
                         changes={'replaced_by_version': roster.version})
        roster.status = R.APPROVED
        roster.approved_by = actor
        roster.approved_at = now
        roster.save(update_fields=['status', 'approved_by', 'approved_at'])
        events.roster_event(roster, _event(roster, R.PENDING_APPROVAL, R.APPROVED, actor,
                                          comments))
        audit.record('roster.approved', entity=roster, actor=actor, reason=comments or None,
                     changes={'version': roster.version, 'vacancy': roster.vacancy.reference,
                              'prepared_by': str(roster.prepared_by_id),
                              'template_version': roster.template.version,
                              'cutoff_at': roster.cutoff_at.isoformat(),
                              'manifest': roster.source_revision_manifest['applications']})
    return []


@transaction.atomic
def return_for_correction(actor, roster, *, comments):
    _require_approver(actor)
    roster = _lock_roster(roster)
    if roster.status != R.PENDING_APPROVAL:
        raise ValidationError('Only versions pending approval can be returned.')
    if not comments.strip():
        raise ValidationError('Explain what needs correction.')
    roster.status = R.RETURNED
    roster.save(update_fields=['status'])
    events.roster_event(roster, _event(roster, R.PENDING_APPROVAL, R.RETURNED, actor,
                                      comments.strip()))
    audit.record('roster.returned', entity=roster, actor=actor, reason=comments.strip(),
                 changes={'version': roster.version})
    return roster


def release(actor, roster):
    """Generate the official Excel and PDF files and record the release.
    If a file cannot be produced, nothing is released and the error is shown."""
    _require_approver(actor)
    roster.refresh_from_db()
    if roster.status != R.APPROVED:
        raise ValidationError('Only the approved version can be released.')
    if roster.released_at:
        raise ValidationError('This version was already released.')
    if roster.requires_regeneration:
        raise ValidationError('Application information changed after approval. Prepare and '
                              'approve a new version instead of releasing this one.')
    existing = {e.format for e in roster.exports.filter(is_draft=False)}
    for fmt in (ExportFormat.XLSX, ExportFormat.PDF):
        if fmt not in existing:
            create_export(actor, roster, fmt, official=True)
    with transaction.atomic():
        roster = _lock_roster(roster)
        if roster.released_at or roster.status != R.APPROVED:
            raise ValidationError('This version can no longer be released.')
        roster.released_by = actor
        roster.released_at = timezone.now()
        roster.save(update_fields=['released_by', 'released_at'])
        audit.record('roster.released', entity=roster, actor=actor,
                     changes={'version': roster.version,
                              'files': list(roster.exports.filter(is_draft=False)
                                            .values_list('sha256', flat=True))})
    return roster


# --------------------------------------------------------------------------
# Exports
# --------------------------------------------------------------------------

def _render(roster, fmt):
    data = exporters.roster_data(roster)
    path = template_path(roster.template)
    if fmt == ExportFormat.XLSX:
        content, _ = exporters.build_xlsx(path, data)
        return content
    return exporters.build_pdf(path, data)


def create_export(actor, roster, fmt, *, official=False):
    """Write a new file (never overwriting) and record it. Raises ExportFailed
    with a readable message; no record or file is left behind on failure."""
    if fmt not in ExportFormat.values:
        raise ValidationError('Unknown export format.')
    if official:
        _require_approver(actor)
        if roster.status != R.APPROVED:
            raise ValidationError('Official files require an approved version.')
    else:
        if not can_view(actor, roster.vacancy):
            raise PermissionDenied
        if roster.status not in ACTIVE:
            raise ValidationError('Draft files can only be produced while the version is in '
                                  'preparation. Use the released files of approved versions.')
    key = f'rosters/{roster.pk}/{uuid.uuid4().hex}.{fmt}'
    path = _safe_path(export_root(), key)
    try:
        content = _render(roster, fmt)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'xb') as handle:
            handle.write(content)
        with transaction.atomic():
            export = RosterExport.objects.create(
                roster=roster, format=fmt, is_draft=not official, storage_key=key,
                sha256=hashlib.sha256(content).hexdigest(), generated_by=actor)
            audit.record('roster.exported', entity=export, actor=actor, changes={
                'roster_version': roster.version, 'format': fmt, 'official': official,
                'sha256': export.sha256})
    except Exception as exc:
        path.unlink(missing_ok=True)
        audit.record('roster.export_failed', entity=roster, actor=actor,
                     changes={'format': fmt, 'official': official, 'error': str(exc)[:500]})
        if isinstance(exc, (PermissionDenied,)):
            raise
        message = '; '.join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        raise ExportFailed(f'The {ExportFormat(fmt).label} file could not be generated: '
                           f'{message}. Nothing was saved; try again.') from exc
    return export


def export_path(export):
    return _safe_path(export_root(), export.storage_key)


def can_download(user, export):
    return can_view(user, export.roster.vacancy)


def record_download(user, export):
    audit.record('roster.export_downloaded', entity=export, actor=user, changes={
        'roster_version': export.roster.version, 'format': export.format,
        'official': not export.is_draft})


# --------------------------------------------------------------------------
# Change detection (signal receiver)
# --------------------------------------------------------------------------

def mark_outdated(sender, application, **kwargs):
    """Runs inside the transaction that changed the application. Marks every
    roster that includes (or would now include) it, without changing content."""
    if not application.submitted_at:
        return
    Vacancy.objects.select_for_update().filter(pk=application.vacancy_id).first()
    rosters = ApplicantRoster.objects.select_for_update().filter(
        vacancy_id=application.vacancy_id, status__in=OUTDATABLE, requires_regeneration=False,
        cutoff_at__gte=application.submitted_at)
    for roster in rosters:
        roster.requires_regeneration = True
        roster.save(update_fields=['requires_regeneration'])
        audit.record('roster.regeneration_required', entity=roster, changes={
            'application': application.reference, 'revision': application.revision})
