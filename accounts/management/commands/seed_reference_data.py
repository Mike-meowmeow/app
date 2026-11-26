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
]

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

    def ensure(self, model, code, name):
        obj, created = model.objects.get_or_create(code=code, defaults={'name': name})
        if created:
            self.changed = True
            audit.record('reference.created', entity=obj, actor=None,
                         changes={'code': code, 'name': name})
            self.stdout.write(f'  {model._meta.verbose_name} {code}: created')
        return obj
