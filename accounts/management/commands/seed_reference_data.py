"""Idempotent seed of roles and baseline reference data.

Safe to run repeatedly: it only creates what is missing and only adds
permissions, never removing ones an administrator granted later.
"""

from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.roles import ROLE_PERMISSIONS
from audit import services as audit
from audit.context import audit_reason
from core.models import RecruitmentType

# Confirmed capstone scope. Other recruitment types need HR confirmation.
RECRUITMENT_TYPES = [('PTI', 'Part-Time Instructor')]


def resolve_permission(dotted):
    app_label, codename = dotted.split('.', 1)
    try:
        return Permission.objects.get(content_type__app_label=app_label, codename=codename)
    except Permission.DoesNotExist:
        raise CommandError(f'Permission {dotted} does not exist. Run migrate first.')


class Command(BaseCommand):
    help = 'Create default roles (groups), their permissions, and baseline reference data.'

    @transaction.atomic
    def handle(self, *args, **options):
        created_anything = False
        with audit_reason('Reference data seed'):
            for role, permission_names in ROLE_PERMISSIONS.items():
                group, created = Group.objects.get_or_create(name=role)
                wanted = {resolve_permission(p) for p in permission_names}
                missing = wanted - set(group.permissions.all())
                if missing:
                    group.permissions.add(*missing)
                if created or missing:
                    created_anything = True
                    audit.record('role.configured', entity=group, actor=None, changes={
                        'created': created,
                        'permissions_added': sorted(f'{p.content_type.app_label}.{p.codename}'
                                                    for p in missing)})
                    self.stdout.write(f'  role {role}: '
                                      f'{"created" if created else "updated"}'
                                      f'{f", +{len(missing)} permissions" if missing else ""}')

            for code, name in RECRUITMENT_TYPES:
                rtype, created = RecruitmentType.objects.get_or_create(
                    code=code, defaults={'name': name})
                if created:
                    created_anything = True
                    audit.record('reference.created', entity=rtype, actor=None,
                                 changes={'code': code, 'name': name})
                    self.stdout.write(f'  recruitment type {code}: created')

        self.stdout.write(self.style.SUCCESS(
            'Reference data seeded.' if created_anything else 'Reference data already up to date.'))
