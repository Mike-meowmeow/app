"""Assign or revoke a role from the command line, e.g. to give the first
System Administrator access. Audited with no actor and the given reason."""

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from accounts import roles, services


class Command(BaseCommand):
    help = 'Assign (or with --revoke, remove) a role for a user.'

    def add_arguments(self, parser):
        parser.add_argument('user', help='username or email')
        parser.add_argument('role', choices=roles.ALL_ROLES, metavar='role',
                            help=f'one of: {", ".join(roles.ALL_ROLES)}')
        parser.add_argument('--revoke', action='store_true')
        parser.add_argument('--reason', default='Changed from the command line')

    def handle(self, *args, user, role, revoke, reason, **options):
        User = get_user_model()
        try:
            target = User.objects.get(Q(username__iexact=user) | Q(email__iexact=user))
        except User.DoesNotExist:
            raise CommandError(f'No user matches {user!r}.')
        current = set(target.groups.values_list('name', flat=True))
        wanted = (current - {role}) if revoke else (current | {role})
        if wanted == current:
            self.stdout.write(f'No change: {target.username} '
                              f'{"does not have" if revoke else "already has"} {role}.')
            return
        services.set_roles(target, sorted(wanted & set(roles.ALL_ROLES)), reason=reason)
        self.stdout.write(self.style.SUCCESS(
            f'{"Revoked" if revoke else "Assigned"} {role} '
            f'{"from" if revoke else "to"} {target.username}.'))
