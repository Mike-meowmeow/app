"""Stage 3 acceptance tests: FR-001 authentication, FR-002 access, FR-003
registration, and audit recording of account events."""

import re
from io import StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from audit.models import AuditLog
from audit.services import record

from . import roles, services
from .access import require_owner
from .models import PrivacyAcknowledgment

User = get_user_model()
PASSWORD = 'Correct-Horse-42'


def seed():
    call_command('seed_reference_data', stdout=StringIO())


def make_user(username, *role_list, verified=True, active=True, acknowledged=True, **extra):
    user = User.objects.create_user(username, f'{username}@example.com', PASSWORD,
                                    first_name=username.title(), is_active=active, **extra)
    if verified:
        user.email_verified_at = timezone.now()
        user.save(update_fields=['email_verified_at'])
    if role_list:
        user.groups.add(*Group.objects.filter(name__in=role_list))
    if acknowledged and roles.APPLICANT in role_list:
        PrivacyAcknowledgment.objects.create(user=user,
                                             notice_version=settings.PRIVACY_NOTICE_VERSION)
    return user


def actions(**filters):
    return list(AuditLog.objects.filter(**filters).values_list('action', flat=True))


class AuthenticationTests(TestCase):
    def setUp(self):
        seed()
        self.user = make_user('ana', roles.APPLICANT)
        self.login_url = reverse('accounts:login')

    def login(self, identifier, password=PASSWORD):
        return self.client.post(self.login_url, {'username': identifier, 'password': password})

    def test_login_with_username_or_email_case_insensitive(self):
        self.assertRedirects(self.login('ANA'), reverse('core:dashboard'),
                             fetch_redirect_response=False)
        self.client.post(reverse('accounts:logout'))
        self.assertRedirects(self.login('Ana@Example.com'), reverse('core:dashboard'),
                             fetch_redirect_response=False)
        self.assertIn('auth.login', actions(actor=self.user))
        self.assertIn('auth.logout', actions(actor=self.user))

    def test_failure_message_is_generic_and_password_never_logged(self):
        wrong_password = self.login('ana', 'wrong-password')
        unknown_user = self.login('nobody', 'wrong-password')
        self.assertContains(wrong_password, 'Incorrect username/email or password.')
        self.assertContains(unknown_user, 'Incorrect username/email or password.')
        failed = AuditLog.objects.filter(action='auth.login_failed')
        self.assertEqual(failed.count(), 2)
        for event in failed:
            self.assertNotIn('wrong-password', str(event.changes))

    def test_disabled_account_cannot_log_in(self):
        self.user.is_active = False
        self.user.save()
        self.assertContains(self.login('ana'), 'Incorrect username/email or password.')

    def test_disabling_account_ends_existing_session(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('core:workspace_applicant')).status_code, 200)
        self.user.is_active = False
        self.user.save()
        response = self.client.get(reverse('core:workspace_applicant'))
        self.assertRedirects(response, f"{reverse('accounts:login')}?next="
                             f"{reverse('core:workspace_applicant')}",
                             fetch_redirect_response=False)

    def test_session_expires_after_configurable_inactivity(self):
        self.assertTrue(settings.SESSION_SAVE_EVERY_REQUEST)
        self.assertEqual(settings.SESSION_COOKIE_AGE, settings.SESSION_IDLE_TIMEOUT_MINUTES * 60)

    def test_unverified_applicant_must_verify_first(self):
        make_user('ben', roles.APPLICANT, verified=False)
        response = self.login('ben')
        self.assertContains(response, 'verify your email address')
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_password_hash_not_plaintext(self):
        self.assertNotIn(PASSWORD, self.user.password)
        self.assertTrue(self.user.check_password(PASSWORD))


class RegistrationTests(TestCase):
    def setUp(self):
        seed()
        self.url = reverse('accounts:register')

    def form_data(self, **overrides):
        data = {'username': 'carla', 'email': 'Carla@Example.com', 'first_name': 'Carla',
                'middle_name': '', 'last_name': 'Reyes', 'password1': PASSWORD,
                'password2': PASSWORD, 'privacy_acknowledged': 'on'}
        data.update(overrides)
        return data

    def test_registration_creates_unverified_applicant_with_acknowledgment(self):
        response = self.client.post(self.url, self.form_data())
        self.assertContains(response, 'Check your email')
        user = User.objects.get(username='carla')
        self.assertEqual(user.email, 'carla@example.com')
        self.assertIsNone(user.email_verified_at)
        self.assertIsNone(user.middle_name)
        self.assertEqual(set(roles.role_names(user)), {roles.APPLICANT})
        ack = user.privacy_acknowledgments.get()
        self.assertEqual(ack.notice_version, settings.PRIVACY_NOTICE_VERSION)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn('/accounts/verify/', mail.outbox[0].body)
        events = actions(entity_id=str(user.pk))
        self.assertIn('account.registered', events)
        self.assertIn('role.assigned', events)

    def test_public_registration_cannot_create_privileged_accounts(self):
        self.client.post(self.url, self.form_data(is_staff='on', is_superuser='on',
                                                  groups=[Group.objects.get(name=roles.HR).pk]))
        user = User.objects.get(username='carla')
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertEqual(set(roles.role_names(user)), {roles.APPLICANT})

    def test_duplicate_email_rejected_case_insensitively(self):
        make_user('existing')
        response = self.client.post(self.url, self.form_data(email='EXISTING@example.com'))
        self.assertContains(response, 'An account with this email address already exists.')
        self.assertFalse(User.objects.filter(username='carla').exists())

    def test_missing_information_is_identified(self):
        response = self.client.post(self.url, {'username': 'carla'})
        for label in ('First name', 'Last name', 'Email', 'Password', 'privacy notice'):
            self.assertContains(response, label)
        self.assertFalse(User.objects.filter(username='carla').exists())

    def test_verification_link_activates_sign_in(self):
        self.client.post(self.url, self.form_data())
        link = re.search(r'https?://\S+', mail.outbox[0].body).group(0)
        self.assertContains(self.client.get(link), 'Email verified')
        user = User.objects.get(username='carla')
        self.assertIsNotNone(user.email_verified_at)
        self.assertIn('account.email_verified', actions(entity_id=str(user.pk)))
        response = self.client.post(reverse('accounts:login'),
                                    {'username': 'carla', 'password': PASSWORD})
        self.assertEqual(response.status_code, 302)

    @override_settings(EMAIL_VERIFICATION_TIMEOUT=-1)
    def test_expired_verification_link(self):
        user = make_user('dina', roles.APPLICANT, verified=False)
        token = services.make_verification_token(user)
        response = self.client.get(reverse('accounts:verify_email', args=[token]))
        self.assertContains(response, 'expired', status_code=400)
        user.refresh_from_db()
        self.assertIsNone(user.email_verified_at)

    def test_tampered_verification_link(self):
        response = self.client.get(reverse('accounts:verify_email', args=['not-a-token']))
        self.assertContains(response, 'not valid', status_code=400)

    def test_resend_gives_same_response_for_unknown_email(self):
        make_user('eli', roles.APPLICANT, verified=False)
        url = reverse('accounts:resend_verification')
        known = self.client.post(url, {'email': 'eli@example.com'})
        unknown = self.client.post(url, {'email': 'ghost@example.com'})
        self.assertEqual(known.content, unknown.content)
        self.assertEqual(len(mail.outbox), 1)


class PasswordResetTests(TestCase):
    def setUp(self):
        seed()
        self.user = make_user('faye', roles.APPLICANT)

    def test_reset_link_is_single_use(self):
        self.client.post(reverse('accounts:password_reset'), {'email': 'faye@example.com'})
        self.assertEqual(len(mail.outbox), 1)
        link = re.search(r'https?://\S+', mail.outbox[0].body).group(0)
        set_password_url = self.client.get(link).url
        response = self.client.post(set_password_url, {'new_password1': 'New-Secret-77',
                                                       'new_password2': 'New-Secret-77'})
        self.assertRedirects(response, reverse('accounts:password_reset_complete'))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('New-Secret-77'))
        self.assertIn('account.password_reset', actions(entity_id=str(self.user.pk)))
        self.assertContains(self.client.get(link, follow=True), 'Reset link not valid')

    def test_unknown_email_gets_same_page_and_no_mail(self):
        response = self.client.post(reverse('accounts:password_reset'),
                                    {'email': 'ghost@example.com'})
        self.assertRedirects(response, reverse('accounts:password_reset_done'))
        self.assertEqual(len(mail.outbox), 0)


class PrivacyNoticeTests(TestCase):
    def setUp(self):
        seed()
        self.user = make_user('gina', roles.APPLICANT)

    def test_new_notice_version_requires_acknowledgment(self):
        self.client.force_login(self.user)
        target = reverse('core:workspace_applicant')
        with self.settings(PRIVACY_NOTICE_VERSION='2027-01'):
            response = self.client.get(target)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response.url.startswith(reverse('accounts:privacy')))
            response = self.client.post(reverse('accounts:privacy'),
                                        {'acknowledged': 'on', 'next': target})
            self.assertRedirects(response, target)
            self.assertTrue(self.user.privacy_acknowledgments.filter(
                notice_version='2027-01').exists())
            self.assertEqual(self.client.get(target).status_code, 200)


class AccessControlTests(TestCase):
    def setUp(self):
        seed()
        self.applicant = make_user('hana', roles.APPLICANT)
        self.other_applicant = make_user('ivan', roles.APPLICANT)
        self.hr = make_user('jose', roles.HR)
        self.admin = make_user('kate', roles.SYSTEM_ADMIN)

    def test_anonymous_users_are_sent_to_login(self):
        response = self.client.get(reverse('core:workspace_hr'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('accounts:login')))

    def test_workspaces_require_matching_role(self):
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(reverse('core:workspace_applicant')).status_code, 200)
        for name in ('core:workspace_hr', 'core:workspace_approver', 'core:workspace_psb',
                     'core:workspace_department', 'accounts:user_list'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 403, name)
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse('core:workspace_hr')).status_code, 200)
        self.assertEqual(self.client.get(reverse('core:workspace_applicant')).status_code, 403)

    def test_single_role_dashboard_opens_that_workspace(self):
        self.client.force_login(self.hr)
        self.assertRedirects(self.client.get(reverse('core:dashboard')),
                             reverse('core:workspace_hr'))

    def test_superuser_and_admin_get_no_recruitment_authority(self):
        root = User.objects.create_superuser('root', 'root@example.com', PASSWORD)
        self.assertTrue(root.has_perm('core.change_department'))  # Django superuser
        self.assertFalse(roles.has_role(root, roles.APPROVER, roles.HR))
        self.assertFalse(roles.has_role(self.admin, roles.APPROVER, roles.HR))
        self.client.force_login(root)
        self.assertEqual(self.client.get(reverse('core:workspace_approver')).status_code, 403)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('core:workspace_approver')).status_code, 403)

    def test_applicant_ownership_check(self):
        require_owner(self.applicant, self.applicant.pk)
        with self.assertRaises(PermissionDenied):
            require_owner(self.applicant, self.other_applicant.pk)

    def test_admin_assigns_and_revokes_roles_with_audit(self):
        self.client.force_login(self.admin)
        url = reverse('accounts:user_access', args=[self.hr.pk])
        response = self.client.post(url, {'roles': [roles.HR, roles.PSB_MEMBER],
                                           'is_active': 'on', 'reason': 'Joined PSB'})
        self.assertRedirects(response, reverse('accounts:user_list'))
        self.hr.refresh_from_db()
        self.assertEqual(set(self.hr.groups.values_list('name', flat=True)),
                         {roles.HR, roles.PSB_MEMBER})
        event = AuditLog.objects.get(action='role.assigned', entity_id=str(self.hr.pk),
                                     changes__role=roles.PSB_MEMBER)
        self.assertEqual(event.actor, self.admin)
        self.assertEqual(event.reason, 'Joined PSB')

        self.client.post(url, {'roles': [roles.PSB_MEMBER], 'is_active': 'on',
                               'reason': 'Left HR'})
        self.assertTrue(AuditLog.objects.filter(action='role.revoked', changes__role=roles.HR,
                                                entity_id=str(self.hr.pk)).exists())

    def test_admin_disables_account_and_session_ends(self):
        hr_client = self.client_class()
        hr_client.force_login(self.hr)
        self.client.force_login(self.admin)
        self.client.post(reverse('accounts:user_access', args=[self.hr.pk]),
                         {'roles': [roles.HR], 'reason': 'Left the institution'})
        self.hr.refresh_from_db()
        self.assertFalse(self.hr.is_active)
        self.assertTrue(AuditLog.objects.filter(action='account.disabled',
                                                entity_id=str(self.hr.pk),
                                                actor=self.admin).exists())
        self.assertEqual(hr_client.get(reverse('core:workspace_hr')).status_code, 302)

    def test_admin_cannot_change_own_access_and_others_cannot_at_all(self):
        with self.assertRaises(PermissionDenied):
            services.update_user_access(self.admin, self.admin, role_names=[roles.APPROVER],
                                        is_active=True, reason='self')
        with self.assertRaises(PermissionDenied):
            services.update_user_access(self.hr, self.applicant, role_names=[roles.HR],
                                        is_active=True, reason='escalate')
        self.client.force_login(self.hr)
        response = self.client.post(reverse('accounts:user_access', args=[self.applicant.pk]),
                                    {'roles': [roles.HR], 'is_active': 'on', 'reason': 'x'})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(set(roles.role_names(self.applicant)), {roles.APPLICANT})

    def test_role_changes_outside_services_are_still_audited(self):
        # e.g. Django admin or shell
        Group.objects.get(name=roles.PSB_MEMBER).user_set.add(self.applicant)
        self.applicant.groups.clear()
        self.assertIn('role.assigned', actions(entity_id=str(self.applicant.pk)))
        self.assertEqual(AuditLog.objects.filter(action='role.revoked',
                                                 entity_id=str(self.applicant.pk)).count(), 2)


class SeedAndAuditTests(TestCase):
    def test_seed_is_idempotent(self):
        seed()
        groups, events = Group.objects.count(), AuditLog.objects.count()
        seed()
        self.assertEqual(Group.objects.count(), groups)
        self.assertEqual(AuditLog.objects.count(), events)
        self.assertEqual(groups, len(roles.ALL_ROLES))

    def test_sensitive_values_are_redacted(self):
        event = record('test.event', entity_type='test', entity_id='1',
                       changes={'password': 'p', 'nested': {'reset_token': 't'}, 'name': 'ok'})
        self.assertEqual(event.changes['password'], '[redacted]')
        self.assertEqual(event.changes['nested']['reset_token'], '[redacted]')
        self.assertEqual(event.changes['name'], 'ok')

    def test_assign_role_command(self):
        seed()
        user = make_user('lara')
        call_command('assign_role', 'lara@example.com', roles.SYSTEM_ADMIN, stdout=StringIO())
        self.assertTrue(roles.has_role(user, roles.SYSTEM_ADMIN))
        event = AuditLog.objects.get(action='role.assigned', entity_id=str(user.pk))
        self.assertIsNone(event.actor)
        self.assertEqual(event.reason, 'Changed from the command line')
