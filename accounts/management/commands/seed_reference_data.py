"""Idempotent seed of roles and baseline reference data.

Safe to run repeatedly: it only creates what is missing and only adds
permissions, never removing ones an administrator granted later or changing
reference records an administrator edited.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.roles import ROLE_PERMISSIONS
from audit import services as audit
from audit.context import audit_reason
from core.credential_list import ELIGIBILITIES, LICENSES
from core.models import Credential, CredentialKind, Department, RecruitmentType
from documents.models import DocumentType

# Confirmed capstone scope. Other recruitment types need HR confirmation.
RECRUITMENT_TYPES = [('PTI', 'Part-Time Instructor')]

# Common part-time instructor application documents. Development defaults:
# the official checklist still needs HR confirmation; HR can deactivate or add.
DOCUMENT_TYPES = [
    ('LETTER', 'Application letter'),
    ('PDS', 'Personal Data Sheet (CSC Form 212)'),
    ('RESUME', 'Resume / Curriculum Vitae'),
    ('TOR', 'Transcript of Records'),
    ('DIPLOMA', 'Diploma'),
    ('PRC', 'PRC license / eligibility certificate'),
    ('TRAINING', 'Training certificates'),
    ('EMPLOYMENT', 'Certificate of employment / service record'),
    # Anything outside the list (awards, recommendations, ...). HR adds it to a
    # vacancy as an optional item with a file limit. Not in AI_EXTRACTION_TYPES:
    # its content varies, so HR reviews it manually.
    ('OTHER', 'Other supporting documents'),
]

# Draft guidelines: what HR checks for each document type (one point per
# line). Written 28 November 2026 for HR to review; they show as a draft until
# HR ticks "guidelines confirmed" in the admin. Only filled where empty, so
# HR's edits are never overwritten.
DOCUMENT_GUIDELINES = {
    'LETTER': [
        'Addressed to the school or HRMDO and names the position applied for',
        'Signed and dated by the applicant',
        'Readable and belongs to this applicant',
    ],
    'PDS': [
        'Current CSC Form 212 (Revised 2017) with all pages',
        'Signed and dated by the applicant, with the right thumbmark',
        'Recent passport-size photo attached',
        'Name, birthdate, and education match the profile and the other documents',
    ],
    'RESUME': [
        'Readable and belongs to this applicant',
        'Education and work history agree with the PDS and the certificates',
    ],
    'TOR': [
        "Name matches the applicant (check maiden or married name)",
        "Registrar's signature and the school's dry seal or official mark",
        'Degree, major, and date of graduation are shown',
        'Every page is included',
        'Remarks such as "Not valid for transfer" or "For employment purposes" are acceptable',
    ],
    'DIPLOMA': [
        'Name, degree, school, and date of graduation are shown',
        'Signed by the school officials, with the school seal',
        'Degree agrees with the Transcript of Records',
    ],
    'PRC': [
        'Name and license or rating number are readable',
        'For a PRC ID: not expired on the submission date (note an expired card as Needs Review)',
        'Can be checked on the PRC online verification service',
        'For a CSC eligibility: certificate or report of rating, with the rating and exam date',
    ],
    'TRAINING': [
        'Title, date, number of hours, and organizer are shown',
        'Signed by the organizer or issuing office',
        'Name matches the applicant',
    ],
    'EMPLOYMENT': [
        'Position, start date, and end date (or "to present") are shown',
        'Shows full-time or part-time, if the vacancy counts only one kind',
        "Signed by the employer's HR or head of office, on letterhead",
        'Dates agree with the PDS work experience',
    ],
    'OTHER': [
        'Relevant to the application (award, recommendation, and similar)',
        'Readable and belongs to this applicant',
        'Reject junk or unrelated files with a reason',
    ],
}

SAMPLE_DEPARTMENT = ('DEV', 'Sample Department (development only)')


def resolve_permission(dotted):
    app_label, codename = dotted.split('.', 1)
    try:
        return Permission.objects.get(content_type__app_label=app_label, codename=codename)
    except Permission.DoesNotExist:
        raise CommandError(f'Permission {dotted} does not exist. Run migrate first.')


class Command(BaseCommand):
    help = 'Create default roles, permissions, and baseline reference data.'

    def add_arguments(self, parser):
        parser.add_argument('--sample-department', action='store_true',
                            help='also create a clearly labeled development department')

    @transaction.atomic
    def handle(self, *args, sample_department=False, **options):
        self.changed = False
        with audit_reason('Reference data seed'):
            self.seed_roles()
            for code, name in RECRUITMENT_TYPES:
                self.ensure(RecruitmentType, code, name)
            for code, name in DOCUMENT_TYPES:
                self.ensure(DocumentType, code, name)
            self.seed_guidelines()
            self.seed_credentials()
            if sample_department:
                self.ensure(Department, *SAMPLE_DEPARTMENT)
        self.stdout.write(self.style.SUCCESS(
            'Reference data seeded.' if self.changed else 'Reference data already up to date.'))

    def seed_roles(self):
        for role, permission_names in ROLE_PERMISSIONS.items():
            group, created = Group.objects.get_or_create(name=role)
            wanted = {resolve_permission(p) for p in permission_names}
            missing = wanted - set(group.permissions.all())
            if missing:
                group.permissions.add(*missing)
            if created or missing:
                self.changed = True
                audit.record('role.configured', entity=group, actor=None, changes={
                    'created': created,
                    'permissions_added': sorted(f'{p.content_type.app_label}.{p.codename}'
                                                for p in missing)})
                self.stdout.write(f'  role {role}: {"created" if created else "updated"}'
                                  f'{f", +{len(missing)} permissions" if missing else ""}')

    def seed_credentials(self):
        """Add missing license and eligibility names (core/credential_list.py)."""
        added = 0
        for kind, entries in ((CredentialKind.LICENSE, LICENSES),
                              (CredentialKind.ELIGIBILITY, ELIGIBILITIES)):
            for name, aliases in entries:
                _, created = Credential.objects.get_or_create(
                    kind=kind, name=name, defaults={'aliases': '\n'.join(aliases)})
                added += created
        if added:
            self.changed = True
            audit.record('reference.credentials_seeded', actor=None,
                         entity_type='credential_names', changes={'added': added})
            self.stdout.write(f'  licenses and eligibilities: {added} added')

    def seed_guidelines(self):
        """Fill the draft guidelines of document types that have none."""
        filled = 0
        for code, points in DOCUMENT_GUIDELINES.items():
            empty = DocumentType.objects.filter(code=code, guidelines='')
            filled += empty.update(guidelines='\n'.join(points))
        if filled:
            self.changed = True
            audit.record('reference.document_guidelines_seeded', actor=None,
                         entity_type='document_types', changes={'filled': filled})
            self.stdout.write(f'  document guidelines (draft): {filled} filled')

    def ensure(self, model, code, name):
        obj, created = model.objects.get_or_create(code=code, defaults={'name': name})
        if created:
            self.changed = True
            audit.record('reference.created', entity=obj, actor=None,
                         changes={'code': code, 'name': name})
            self.stdout.write(f'  {model._meta.verbose_name} {code}: created')
        return obj
