"""Audit authentication events and every role (group) change, wherever it
happens: our services, the Django admin, or management commands."""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.contrib.auth.signals import user_logged_in, user_logged_out, user_login_failed
from django.db.models.signals import m2m_changed
from django.dispatch import receiver

from audit import services as audit
from audit.context import client_ip

from . import roles

User = get_user_model()


@receiver(user_logged_in)
def audit_login(sender, request, user, **kwargs):
    audit.record('auth.login', entity=user, actor=user, ip_address=client_ip(request))


@receiver(user_logged_out)
def audit_logout(sender, request, user, **kwargs):
    if user is not None:
        audit.record('auth.logout', entity=user, actor=user, ip_address=client_ip(request))


@receiver(user_login_failed)
def audit_login_failed(sender, credentials, request=None, **kwargs):
    # Django masks the password in `credentials`; only the identifier is kept.
    identifier = str(credentials.get('username', ''))[:150]
    match = getattr(request, 'resolver_match', None)
    audience = 'staff' if match and match.view_name == 'accounts:staff_login' else 'applicant'
    audit.record('auth.login_failed', entity_type='auth', entity_id='', actor=None,
                 changes={'identifier': identifier, 'audience': audience},
                 ip_address=client_ip(request))


def _check_new_roles(user, added):
    current = set(user.groups.values_list('name', flat=True))
    roles.validate_role_change(current, current | set(added), is_superuser=user.is_superuser)


@receiver(m2m_changed, sender=User.groups.through)
def keep_account_kinds_separate(sender, instance, action, reverse, pk_set, **kwargs):
    """Last line of defense, wherever a role is added from."""
    if action != 'pre_add' or not pk_set:
        return
    if reverse:
        for user in User.objects.filter(pk__in=pk_set):
            _check_new_roles(user, [instance.name])
    else:
        _check_new_roles(instance, Group.objects.filter(pk__in=pk_set)
                         .values_list('name', flat=True))


@receiver(m2m_changed, sender=User.groups.through)
def audit_role_change(sender, instance, action, reverse, pk_set, **kwargs):
    if action == 'pre_clear':
        # Remember what is about to be removed; post_clear has no pk_set.
        if reverse:
            instance._cleared_pks = set(instance.user_set.values_list('pk', flat=True))
        else:
            instance._cleared_pks = set(instance.groups.values_list('pk', flat=True))
        return
    if action not in ('post_add', 'post_remove', 'post_clear'):
        return
    if action == 'post_clear':
        pk_set = getattr(instance, '_cleared_pks', set())
    if not pk_set:
        return
    event = 'role.assigned' if action == 'post_add' else 'role.revoked'

    if reverse:  # group.user_set.add(...)
        group = instance
        for user in User.objects.filter(pk__in=pk_set):
            audit.record(event, entity=user, changes={'role': group.name})
    else:        # user.groups.add(...)
        for group in Group.objects.filter(pk__in=pk_set):
            audit.record(event, entity=instance, changes={'role': group.name})
