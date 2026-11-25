"""Staff two-factor sign-in (accounts/two_factor.py)."""

import time

from django.contrib.auth import SESSION_KEY
from django.test import TestCase, override_settings
from django.urls import reverse

from audit.models import AuditLog

from . import roles, two_factor
from .models import StaffRecoveryCode, StaffTwoFactor
from .test_stage3 import PASSWORD, make_user, seed

REQUIRED = override_settings(STAFF_TWO_FACTOR_REQUIRED=True)


def now_code(secret, offset=0):
    return two_factor.code_at(secret, two_factor.current_step() + offset)


def enroll(user):
    device = two_factor.start_setup(user)
    codes = two_factor.confirm_setup(user, now_code(device.secret, -1))
    device.refresh_from_db()
    return device, codes


class TotpTests(TestCase):
    def test_rfc_6238_reference_values(self):
        # RFC 6238 appendix B, SHA-1 key "12345678901234567890", 6 digits.
        secret = 'GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ'
        for when, expected in ((59, '287082'), (1111111109, '081804'),
                               (1234567890, '005924'), (2000000000, '279037')):
            self.assertEqual(two_factor.code_at(secret, two_factor.current_step(when)),
                             expected)

    def test_provisioning_uri_and_qr(self):
        seed()
        user = make_user('hr2fa', roles.HR)
        uri = two_factor.provisioning_uri(user, 'ABCDEFGH')
        self.assertTrue(uri.startswith('otpauth://totp/EVSU%20Recruitment%3Ahr2fa?'))
        self.assertIn('secret=ABCDEFGH', uri)
        self.assertTrue(two_factor.qr_png_data_uri(uri).startswith('data:image/png;base64,'))


class TwoFactorCase(TestCase):
    def setUp(self):
        seed()
        self.hr = make_user('hrone', roles.HR)
        self.applicant = make_user('appl', roles.APPLICANT)
        self.admin = make_user('sysadm', roles.SYSTEM_ADMIN)

    def password_step(self, username='hrone', **data):
        return self.client.post(reverse('accounts:staff_login'),
                                {'username': username, 'password': PASSWORD, **data})

    def signed_in(self):
        return SESSION_KEY in self.client.session


@REQUIRED
class RequiredSignInTests(TwoFactorCase):
    def test_password_alone_does_not_sign_in_and_leads_to_setup(self):
        response = self.password_step()
        self.assertRedirects(response, reverse('accounts:two_factor_setup'))
        self.assertFalse(self.signed_in())
        self.assertFalse(AuditLog.objects.filter(action='auth.login').exists())

    def test_first_sign_in_sets_up_the_app_and_shows_recovery_codes(self):
        self.password_step()
        page = self.client.get(reverse('accounts:two_factor_setup'))
        self.assertContains(page, 'data:image/png;base64,')
        secret = StaffTwoFactor.objects.get(user=self.hr).secret
        self.assertContains(page, two_factor.grouped(secret))
        response = self.client.post(reverse('accounts:two_factor_setup'),
                                    {'code': now_code(secret)})
        self.assertContains(response, 'Two-factor sign-in is on')
        self.assertEqual(len(response.context['codes']), two_factor.RECOVERY_CODE_COUNT)
        self.assertTrue(self.signed_in())
        self.assertTrue(two_factor.is_enrolled(self.hr))
        self.assertTrue(AuditLog.objects.filter(action='auth.two_factor_enabled').exists())
        self.assertContains(self.client.get(reverse('accounts:account')), 'since')

    def test_wrong_setup_code_keeps_it_off(self):
        self.password_step()
        self.client.get(reverse('accounts:two_factor_setup'))
        response = self.client.post(reverse('accounts:two_factor_setup'), {'code': '000000'})
        self.assertContains(response, 'does not match')
        self.assertFalse(two_factor.is_enrolled(self.hr))
        self.assertFalse(self.signed_in())

    def test_enrolled_staff_enter_a_code_and_return_to_their_page(self):
        device, _ = enroll(self.hr)
        target = reverse('accounts:account')
        response = self.client.post(f"{reverse('accounts:staff_login')}?next={target}",
                                    {'username': 'hrone', 'password': PASSWORD})
        self.assertRedirects(response, reverse('accounts:two_factor_verify'))
        response = self.client.post(reverse('accounts:two_factor_verify'),
                                    {'code': now_code(device.secret, 1)})
        self.assertRedirects(response, target)
        self.assertTrue(self.signed_in())
        self.assertTrue(AuditLog.objects.filter(action='auth.login').exists())

    def test_a_code_works_only_once(self):
        device, _ = enroll(self.hr)
        code = now_code(device.secret)
        self.assertEqual(two_factor.verify(self.hr, code), 'app')
        self.assertIsNone(two_factor.verify(self.hr, code))

    def test_old_codes_are_refused(self):
        device, _ = enroll(self.hr)
        self.assertIsNone(two_factor.verify(self.hr, now_code(device.secret, -5)))

    def test_recovery_code_works_once(self):
        _, codes = enroll(self.hr)
        self.password_step()
        response = self.client.post(reverse('accounts:two_factor_verify'),
                                    {'code': codes[0].lower().replace('-', ' ')})
        self.assertRedirects(response, reverse('core:dashboard'), fetch_redirect_response=False)
        self.assertEqual(two_factor.recovery_codes_left(self.hr),
                         two_factor.RECOVERY_CODE_COUNT - 1)
        self.assertIsNone(two_factor.verify(self.hr, codes[0]))
        self.assertTrue(StaffRecoveryCode.objects.filter(user=self.hr, used_at__isnull=False)
                        .exists())

    def test_recovery_codes_are_stored_only_as_hashes(self):
        _, codes = enroll(self.hr)
        stored = set(StaffRecoveryCode.objects.values_list('code_hash', flat=True))
        self.assertEqual(len(stored), len(codes))
        for code in codes:
            self.assertNotIn(code, stored)
            self.assertNotIn(two_factor.normalize(code), stored)

    def test_wrong_codes_are_counted_and_end_the_attempt(self):
        enroll(self.hr)
        self.password_step()
        from .views import TWO_FACTOR_MAX_TRIES
        for _ in range(TWO_FACTOR_MAX_TRIES):
            response = self.client.post(reverse('accounts:two_factor_verify'),
                                        {'code': '000000'})
        self.assertRedirects(response, reverse('accounts:staff_login'))
        self.assertFalse(self.signed_in())
        failures = AuditLog.objects.filter(action='auth.login_failed',
                                           changes__audience='staff')
        self.assertEqual(failures.count(), TWO_FACTOR_MAX_TRIES)

    @override_settings(LOGIN_MAX_FAILURES=2)
    def test_wrong_codes_lock_the_account_like_wrong_passwords(self):
        device, _ = enroll(self.hr)
        for _ in range(2):
            self.password_step()
            self.client.post(reverse('accounts:two_factor_verify'), {'code': '000000'})
        response = self.password_step()
        self.assertContains(response, 'Too many failed sign-in attempts')

    def test_waiting_sign_in_expires(self):
        enroll(self.hr)
        self.password_step()
        session = self.client.session
        data = session['two_factor_pending']
        data['started'] = time.time() - 600
        session['two_factor_pending'] = data
        session.save()
        response = self.client.post(reverse('accounts:two_factor_verify'), {'code': '123456'})
        self.assertRedirects(response, reverse('accounts:staff_login'))

    def test_code_page_needs_a_correct_password_first(self):
        enroll(self.hr)
        response = self.client.get(reverse('accounts:two_factor_verify'))
        self.assertRedirects(response, reverse('accounts:staff_login'))
        response = self.client.get(reverse('accounts:two_factor_setup'))
        self.assertRedirects(response, reverse('accounts:staff_login'))

    def test_session_without_the_second_step_is_signed_out(self):
        # For example the Django admin's own sign-in, or any other password-only path.
        self.client.force_login(self.hr)
        response = self.client.get(reverse('accounts:account'))
        self.assertRedirects(response, f"{reverse('accounts:staff_login')}?next="
                                       f"{reverse('accounts:account')}")
        self.assertFalse(self.signed_in())

    def test_admin_sign_in_goes_through_the_staff_page(self):
        response = self.client.get('/admin/login/?next=/admin/')
        self.assertRedirects(response, f"{reverse('accounts:staff_login')}?next=/admin/",
                             fetch_redirect_response=False)

    def test_applicants_never_use_it(self):
        response = self.client.post(reverse('accounts:login'),
                                    {'username': 'appl', 'password': PASSWORD})
        self.assertRedirects(response, reverse('core:dashboard'), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse('accounts:account')).status_code, 200)
        self.assertFalse(StaffTwoFactor.objects.exists())

    def test_applicant_cannot_set_it_up(self):
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(reverse('accounts:two_factor_setup')).status_code, 403)

    @override_settings(SITE_PORTAL='public')
    def test_two_factor_pages_are_absent_from_the_public_site(self):
        for name in ('accounts:two_factor_verify', 'accounts:two_factor_setup',
                     'accounts:two_factor_recovery_codes'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 404, name)

    @override_settings(SITE_PORTAL='staff')
    def test_sign_in_works_on_the_staff_site(self):
        device, _ = enroll(self.hr)
        self.password_step()
        response = self.client.post(reverse('accounts:two_factor_verify'),
                                    {'code': now_code(device.secret)})
        self.assertRedirects(response, reverse('core:dashboard'), fetch_redirect_response=False)
        self.assertTrue(self.signed_in())


@override_settings(STAFF_TWO_FACTOR_REQUIRED=False)
class OptionalTests(TwoFactorCase):
    def test_not_required_staff_without_it_sign_in_with_a_password(self):
        response = self.password_step()
        self.assertRedirects(response, reverse('core:dashboard'), fetch_redirect_response=False)
        self.assertTrue(self.signed_in())

    def test_staff_who_set_it_up_are_always_asked(self):
        enroll(self.hr)
        self.assertRedirects(self.password_step(), reverse('accounts:two_factor_verify'))
        self.assertFalse(self.signed_in())

    def test_staff_can_turn_it_on_from_my_account(self):
        self.client.force_login(self.hr)
        self.assertContains(self.client.get(reverse('accounts:account')),
                            'Set up two-factor sign-in')
        self.client.get(reverse('accounts:two_factor_setup'))
        secret = StaffTwoFactor.objects.get(user=self.hr).secret
        response = self.client.post(reverse('accounts:two_factor_setup'),
                                    {'code': now_code(secret)})
        self.assertContains(response, 'Two-factor sign-in is on')
        # Still signed in on this session.
        self.assertEqual(self.client.get(reverse('accounts:account')).status_code, 200)

    def test_new_recovery_codes_need_an_app_code(self):
        device, old = enroll(self.hr)
        self.client.force_login(self.hr)
        session = self.client.session
        session['two_factor_ok'] = True
        session.save()
        url = reverse('accounts:two_factor_recovery_codes')
        self.assertContains(self.client.post(url, {'code': '000000'}), 'not correct')
        response = self.client.post(url, {'code': now_code(device.secret, 1)})
        new = response.context['codes']
        self.assertNotEqual(set(new), set(old))
        self.assertIsNone(two_factor.verify(self.hr, old[0]))
        self.assertEqual(two_factor.verify(self.hr, new[0]), 'recovery')


class ResetTests(TwoFactorCase):
    def setUp(self):
        super().setUp()
        self.admin2 = make_user('sysadm2', roles.SYSTEM_ADMIN)
        enroll(self.hr)
        enroll(self.admin)
        self.client.force_login(self.admin)
        session = self.client.session
        session['two_factor_ok'] = True
        session.save()

    def test_system_admin_resets_with_a_reason(self):
        url = reverse('accounts:two_factor_reset', args=[self.hr.pk])
        response = self.client.post(url, {'reason': 'Lost phone'})
        self.assertRedirects(response, reverse('accounts:user_access', args=[self.hr.pk]))
        self.assertFalse(StaffTwoFactor.objects.filter(user=self.hr).exists())
        self.assertFalse(StaffRecoveryCode.objects.filter(user=self.hr).exists())
        event = AuditLog.objects.get(action='auth.two_factor_reset')
        self.assertEqual(event.reason, 'Lost phone')
        self.assertEqual(event.actor, self.admin)

    def test_reason_is_required(self):
        self.client.post(reverse('accounts:two_factor_reset', args=[self.hr.pk]), {'reason': ''})
        self.assertTrue(two_factor.is_enrolled(self.hr))

    def test_admins_cannot_reset_their_own(self):
        self.client.post(reverse('accounts:two_factor_reset', args=[self.admin.pk]),
                         {'reason': 'mine'})
        self.assertTrue(two_factor.is_enrolled(self.admin))

    def test_only_system_admins_can_reset(self):
        self.client.force_login(self.hr)
        session = self.client.session
        session['two_factor_ok'] = True
        session.save()
        response = self.client.post(reverse('accounts:two_factor_reset', args=[self.admin.pk]),
                                    {'reason': 'x'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(two_factor.is_enrolled(self.admin))

    def test_access_page_shows_status_and_reset(self):
        page = self.client.get(reverse('accounts:user_access', args=[self.hr.pk]))
        self.assertContains(page, 'Reset two-factor sign-in')
