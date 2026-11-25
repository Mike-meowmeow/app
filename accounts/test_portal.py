"""Separate applicant (public) and staff sites and accounts."""

import re
import sys
import uuid
from io import StringIO

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import mail
from django.core.exceptions import ImproperlyConfigured, PermissionDenied
from django.core.management import CommandError, call_command
from django.db import transaction
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver, resolve, reverse

from audit.models import AuditLog
from documents.models import DocumentVersion, MalwareStatus
from notifications.models import Notification
from notifications.services import _email_body
from recruitment.test_applications import ApplicationTestCase, pdf

from . import portal, roles, services
from .test_stage3 import PASSWORD, make_user, seed

User = get_user_model()
PUBLIC = override_settings(SITE_PORTAL='public')
STAFF = override_settings(SITE_PORTAL='staff')
SITES = override_settings(PUBLIC_SITE_URL='https://apply.example.edu',
                          STAFF_SITE_URL='https://staff.example.edu')
BAD_LOGIN = 'Incorrect username/email or password.'


def all_paths(patterns=None, prefix=''):
    """A concrete path for every project URL (the Django admin is tested apart)."""
    values = {'uuid': str(uuid.uuid4()), 'slug': 'x', 'int': '1', 'str': 'x'}
    for entry in patterns if patterns is not None else get_resolver().url_patterns:
        route = prefix + str(entry.pattern)
        if isinstance(entry, URLResolver):
            if not route.startswith('admin/'):
                yield from all_paths(entry.url_patterns, route)
        elif isinstance(entry, URLPattern):
            yield '/' + re.sub(r'<(?:(\w+):)?\w+>',
                               lambda m: values[m.group(1) or 'str'], route)


class PortalCase(TestCase):
    def setUp(self):
        seed()
        self.applicant = make_user('appl', roles.APPLICANT)
        self.hr = make_user('hrone', roles.HR)
        self.admin = make_user('sysadm', roles.SYSTEM_ADMIN)

    def sign_in(self, name, username, password=PASSWORD, **extra):
        return self.client.post(reverse(name), {'username': username, 'password': password},
                                **extra)


class SeparateSignInTests(PortalCase):
    def test_each_page_accepts_only_its_own_accounts(self):
        self.assertEqual(self.sign_in('accounts:login', 'appl').status_code, 302)
        self.client.logout()
        self.assertEqual(self.sign_in('accounts:staff_login', 'hrone').status_code, 302)
        self.client.logout()
        self.assertContains(self.sign_in('accounts:login', 'hrone'), BAD_LOGIN)
        self.assertContains(self.sign_in('accounts:staff_login', 'appl'), BAD_LOGIN)
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_wrong_page_counts_as_a_failed_sign_in(self):
        self.sign_in('accounts:login', 'hrone')
        event = AuditLog.objects.get(action='auth.login_failed')
        self.assertEqual(event.changes['identifier'], 'hrone')

    def test_account_without_a_role_cannot_sign_in_anywhere(self):
        make_user('norole')
        self.assertContains(self.sign_in('accounts:login', 'norole'), BAD_LOGIN)
        self.assertContains(self.sign_in('accounts:staff_login', 'norole'), BAD_LOGIN)

    @override_settings(LOGIN_MAX_FAILURES=2)
    def test_public_failures_cannot_lock_staff_out(self):
        for _ in range(3):
            self.sign_in('accounts:login', 'hrone', 'wrong')
        self.assertContains(self.sign_in('accounts:login', 'hrone'), 'Too many failed')
        self.assertEqual(self.sign_in('accounts:staff_login', 'hrone').status_code, 302)

    @override_settings(LOGIN_MAX_FAILURES_PER_IP=2, STAFF_LOGIN_MAX_FAILURES_PER_IP=50)
    def test_staff_have_their_own_address_limit(self):
        for number in range(3):
            self.sign_in('accounts:staff_login', f'nobody{number}', 'wrong')
        self.assertEqual(self.sign_in('accounts:staff_login', 'hrone').status_code, 302)

    def test_vacancies_menu_link_is_only_for_signed_out_visitors(self):
        link = 'nav-link" href="/recruitment/vacancies/"'
        self.assertContains(self.client.get(reverse('core:home')), link)
        self.client.force_login(self.hr)
        self.assertNotContains(self.client.get(reverse('core:workspace_hr')), link)
        self.client.force_login(self.applicant)
        self.assertNotContains(self.client.get(reverse('core:workspace_applicant')), link)

    def test_logout_returns_staff_to_staff_sign_in(self):
        self.client.force_login(self.hr)
        self.assertRedirects(self.client.post(reverse('accounts:logout')),
                             reverse('accounts:staff_login'))


@STAFF
class StaleSignInPageTests(PortalCase):
    def setUp(self):
        super().setUp()
        self.browser = Client(enforce_csrf_checks=True)
        self.url = reverse('accounts:staff_login')
        self.browser.get(self.url)
        self.old_token = self.browser.cookies[settings.CSRF_COOKIE_NAME].value

    def post(self, url=None):
        return self.browser.post(url or self.url, {
            'username': 'hrone', 'password': PASSWORD, 'csrfmiddlewaretoken': self.old_token})

    def test_tab_left_open_after_signing_in_elsewhere_recovers(self):
        self.assertEqual(self.post().status_code, 302)      # first tab signs in
        response = self.post()                              # stale second tab
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertRedirects(self.browser.get(self.url), reverse('core:dashboard'),
                             fetch_redirect_response=False)

    def test_stale_page_never_signs_in(self):
        self.old_token = 'x' * 64
        response = self.post()
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertNotIn('_auth_user_id', self.browser.session)
        self.assertContains(self.browser.get(self.url), 'had expired')

    def test_other_forms_still_get_403(self):
        self.old_token = 'x' * 64
        self.assertEqual(self.post(reverse('accounts:password_reset')).status_code, 403)


@PUBLIC
class PublicSiteTests(PortalCase):
    def test_every_page_not_on_the_allowlist_is_absent(self):
        self.client.force_login(self.applicant)
        checked = 0
        for path in all_paths():
            if resolve(path).view_name not in portal.PUBLIC_VIEWS:
                checked += 1
                self.assertEqual(self.client.get(path).status_code, 404, path)
                self.assertEqual(self.client.post(path).status_code, 404, path)
        self.assertGreater(checked, 60)

    def test_admin_and_staff_sign_in_are_absent_when_signed_out(self):
        for path in ('/admin/', '/admin/login/', reverse('accounts:staff_login'),
                     reverse('recruitment:vacancy_list'), reverse('audit:list')):
            self.assertEqual(self.client.get(path).status_code, 404, path)

    def test_allowlist_holds_no_staff_area(self):
        for name in portal.PUBLIC_VIEWS:
            self.assertNotIn(name.split(':')[0], ('admin', 'matrix', 'committee', 'analytics',
                                                  'audit', 'operations'))

    def test_applicant_can_register_and_sign_in(self):
        self.assertEqual(self.client.get(reverse('accounts:register')).status_code, 200)
        self.assertEqual(self.sign_in('accounts:login', 'appl').status_code, 302)
        self.assertEqual(self.client.get(reverse('core:dashboard'), follow=True).status_code, 200)

    def test_pages_do_not_advertise_the_staff_site(self):
        for name in ('core:home', 'accounts:login'):
            self.assertNotContains(self.client.get(reverse(name)), 'taff sign in')

    def test_staff_session_is_ended(self):
        self.client.force_login(self.hr)
        response = self.client.get(reverse('recruitment:public_list'))
        self.assertRedirects(response, reverse('accounts:login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_password_reset_ignores_staff_accounts(self):
        self.client.post(reverse('accounts:password_reset'), {'email': self.hr.email})
        self.assertEqual(mail.outbox, [])
        self.client.post(reverse('accounts:password_reset'), {'email': self.applicant.email})
        self.assertEqual(len(mail.outbox), 1)


@STAFF
class StaffSiteTests(PortalCase):
    def test_signed_out_visitors_only_reach_sign_in_and_password_reset(self):
        login = reverse('accounts:staff_login')
        self.assertEqual(self.client.get(login).status_code, 200)
        self.assertEqual(self.client.get(reverse('accounts:password_reset')).status_code, 200)
        self.assertRedirects(self.client.get('/'), login)
        for name in ('accounts:register', 'accounts:login', 'recruitment:public_list',
                     'recruitment:vacancy_list'):
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 302, name)
            self.assertTrue(response.url.startswith(login), name)

    def test_staff_sign_in_and_work(self):
        self.assertEqual(self.sign_in('accounts:staff_login', 'hrone').status_code, 302)
        self.assertEqual(self.client.get(reverse('recruitment:vacancy_list')).status_code, 200)

    def test_applicant_pages_are_absent(self):
        self.client.force_login(self.hr)
        for name in ('accounts:register', 'applicants:profile', 'recruitment:my_applications'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 404, name)

    def test_applicant_session_is_ended(self):
        self.client.force_login(self.applicant)
        self.assertRedirects(self.client.get(reverse('core:dashboard')),
                             reverse('accounts:staff_login'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_password_reset_ignores_applicants(self):
        self.client.post(reverse('accounts:password_reset'), {'email': self.applicant.email})
        self.assertEqual(mail.outbox, [])


class StartupTests(TestCase):
    def build(self):
        return portal.PortalMiddleware(lambda request: HttpResponse())

    @override_settings(PORTAL_ENFORCED=True, SITE_PORTAL='all')
    def test_production_needs_a_specific_site(self):
        with self.assertRaises(ImproperlyConfigured):
            self.build()

    @override_settings(PORTAL_ENFORCED=True, SITE_PORTAL='public', MALWARE_SCAN_COMMAND='')
    def test_public_production_needs_a_malware_scanner(self):
        with self.assertRaises(ImproperlyConfigured):
            self.build()

    @override_settings(PORTAL_ENFORCED=True, SITE_PORTAL='staff', MALWARE_SCAN_COMMAND='')
    def test_staff_production_starts(self):
        self.assertEqual(self.build()(RequestFactory().get('/')).status_code, 200)


@SITES
class EmailLinkTests(PortalCase):
    def link(self):
        return re.search(r'https?://\S+', mail.outbox[-1].body).group(0)

    def test_password_reset_links_point_to_the_accounts_own_site(self):
        self.client.post(reverse('accounts:password_reset'), {'email': self.applicant.email})
        self.assertTrue(self.link().startswith('https://apply.example.edu/'))
        self.client.post(reverse('accounts:password_reset'), {'email': self.hr.email})
        self.assertTrue(self.link().startswith('https://staff.example.edu/'))

    def test_verification_link_ignores_the_request_host(self):
        user = make_user('newbie', roles.APPLICANT, verified=False)
        request = RequestFactory().get('/', HTTP_HOST='evil.example')
        services.send_verification_email(request, user)
        self.assertTrue(self.link().startswith('https://apply.example.edu/accounts/verify/'))

    def test_notification_links_follow_the_recipient(self):
        for user, site in ((self.applicant, 'apply'), (self.hr, 'staff')):
            note = Notification.objects.create(user=user, event_key=f'k-{user.pk}',
                                               event_type='status', title='T', message='M')
            self.assertIn(f'https://{site}.example.edu/', _email_body(note))


class SeparateAccountTests(PortalCase):
    def test_one_account_cannot_hold_both_kinds_of_role(self):
        psb = Group.objects.get(name=roles.PSB_MEMBER)
        applicant_role = Group.objects.get(name=roles.APPLICANT)
        for change in (lambda: self.applicant.groups.add(psb),
                       lambda: psb.user_set.add(self.applicant),
                       lambda: self.hr.groups.add(applicant_role)):
            with self.assertRaises(roles.RoleConflict), transaction.atomic():
                change()

    def test_accounts_are_not_converted(self):
        with self.assertRaises(roles.RoleConflict):
            services.set_roles(self.applicant, [roles.HR])
        with self.assertRaises(roles.RoleConflict):
            services.set_roles(self.hr, [roles.APPLICANT])
        self.assertEqual(set(roles.role_names(self.applicant)), {roles.APPLICANT})

    def test_role_command_reports_the_conflict(self):
        with self.assertRaises(CommandError):
            call_command('assign_role', 'appl', roles.HR, stdout=StringIO())

    def test_access_screen_offers_applicants_no_staff_role(self):
        self.client.force_login(self.admin)
        url = reverse('accounts:user_access', args=[self.applicant.pk])
        self.assertNotContains(self.client.get(url), f'value="{roles.HR}"')
        response = self.client.post(url, {'roles': [roles.HR], 'is_active': 'on', 'reason': 'x'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(set(roles.role_names(self.applicant)), {roles.APPLICANT})


@SITES
class StaffAccountCreationTests(PortalCase):
    data = {'username': 'newhr', 'email': 'NewHR@example.com', 'first_name': 'New',
            'last_name': 'Staff', 'roles': [roles.HR, roles.PSB_MEMBER], 'reason': 'Hired'}

    def test_only_system_administrators_create_staff(self):
        url = reverse('accounts:staff_create')
        for user in (self.hr, self.applicant, make_user('head', roles.APPROVER)):
            self.client.force_login(user)
            self.assertEqual(self.client.post(url, self.data).status_code, 403)
        with self.assertRaises(PermissionDenied):
            services.create_staff_account(self.hr, username='x', email='x@example.com',
                                          first_name='X', last_name='Y',
                                          role_names=[roles.HR], reason='r')
        self.assertFalse(User.objects.filter(username='newhr').exists())

    def test_created_account_sets_password_by_emailed_link_then_signs_in(self):
        self.client.force_login(self.admin)
        response = self.client.post(reverse('accounts:staff_create'), self.data)
        user = User.objects.get(username='newhr')
        self.assertRedirects(response, reverse('accounts:user_access', args=[user.pk]))
        self.assertFalse(user.has_usable_password())
        self.assertEqual(set(roles.role_names(user)), {roles.HR, roles.PSB_MEMBER})
        event = AuditLog.objects.get(action='account.staff_created', entity_id=str(user.pk))
        self.assertEqual((event.actor, event.reason), (self.admin, 'Hired'))
        self.assertTrue(AuditLog.objects.filter(action='account.staff_invitation_sent',
                                                entity_id=str(user.pk)).exists())

        link = re.search(r'https://staff\.example\.edu\S+', mail.outbox[0].body).group(0)
        self.client.logout()
        form_url = self.client.get(link).url
        self.client.post(form_url, {'new_password1': 'Fresh-Secret-81',
                                    'new_password2': 'Fresh-Secret-81'})
        response = self.sign_in('accounts:staff_login', 'newhr', 'Fresh-Secret-81')
        self.assertEqual(response.status_code, 302)

    def test_form_offers_only_staff_roles_and_rejects_duplicates(self):
        self.client.force_login(self.admin)
        url = reverse('accounts:staff_create')
        self.assertNotContains(self.client.get(url), f'value="{roles.APPLICANT}"')
        response = self.client.post(url, {**self.data, 'email': self.hr.email,
                                          'roles': [roles.APPLICANT]})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newhr').exists())

    def test_invitation_can_be_sent_again_only_for_staff(self):
        self.client.force_login(self.admin)
        self.client.post(reverse('accounts:staff_invite', args=[self.hr.pk]))
        self.assertEqual(mail.outbox[-1].to, [self.hr.email])
        response = self.client.post(reverse('accounts:staff_invite', args=[self.applicant.pk]))
        self.assertEqual(response.status_code, 403)

    def test_public_registration_only_creates_applicants(self):
        response = self.client.post(reverse('accounts:register'), {
            'username': 'walkin', 'email': 'walkin@example.com', 'first_name': 'W',
            'last_name': 'I', 'password1': PASSWORD, 'password2': PASSWORD,
            'privacy_acknowledged': 'on', 'roles': [roles.SYSTEM_ADMIN], 'is_staff': 'on'})
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(username='walkin')
        self.assertEqual(set(roles.role_names(user)), {roles.APPLICANT})
        self.assertFalse(user.is_staff)


class ApplicantJourneyTests(ApplicationTestCase):
    @PUBLIC
    def test_every_link_on_applicant_pages_exists_on_the_public_site(self):
        application = self.submitted()
        version = DocumentVersion.objects.get(document__application=application)
        self.client.force_login(self.applicant)
        pending = [reverse(name) for name in (
            'core:workspace_applicant', 'applicants:profile', 'recruitment:my_applications',
            'recruitment:public_list', 'accounts:account', 'notifications:list')]
        pending += [reverse('recruitment:my_application', args=[application.pk]),
                    reverse('recruitment:public_detail', args=[self.vacancy.pk])]
        seen = set()
        while pending:
            path = pending.pop()
            if path in seen or path.startswith('/static/'):
                continue
            seen.add(path)
            response = self.client.get(path)
            self.assertNotEqual(response.status_code, 404, path)
            if response.status_code == 200 and 'html' in response.get('Content-Type', ''):
                pending += re.findall(r'href="(/[^"#?]*)', response.content.decode())
        self.assertIn(reverse('documents:download', args=[version.pk]), seen)

    def upload(self, **settings):
        application = self.draft()
        self.client.force_login(self.applicant)
        with override_settings(MALWARE_SCAN_REQUIRED=True, **settings):
            self.client.post(reverse('recruitment:upload_document', args=[application.pk]),
                             {'document_type': self.pds.pk, 'file': pdf()})

    def test_public_upload_is_refused_when_it_cannot_be_scanned(self):
        self.upload(MALWARE_SCAN_COMMAND='')
        self.assertFalse(DocumentVersion.objects.exists())
        self.assertEqual(self.stored_files(), [])

    def test_clean_scan_is_accepted_when_required(self):
        script = self.storage_dir + '/scan_ok.py'
        with open(script, 'w', encoding='utf-8') as handle:
            handle.write('pass\n')
        self.upload(MALWARE_SCAN_COMMAND=f'{sys.executable} {script}')
        self.assertEqual(DocumentVersion.objects.get().malware_status, MalwareStatus.CLEAN)
