"""Show password buttons on the sign-in and registration pages."""

from django.test import TestCase
from django.urls import reverse


class PasswordToggleTests(TestCase):
    def test_sign_in_pages_have_a_show_button(self):
        for name in ('accounts:login', 'accounts:staff_login'):
            page = self.client.get(reverse(name))
            self.assertContains(page, 'data-password-toggle="id_password"')
            self.assertContains(page, 'core/password_toggle.js?v=')

    def test_registration_has_a_show_button_for_both_passwords(self):
        page = self.client.get(reverse('accounts:register'))
        self.assertContains(page, 'data-password-toggle="id_password1"')
        self.assertContains(page, 'data-password-toggle="id_password2"')

    def test_other_fields_have_no_button(self):
        page = self.client.get(reverse('accounts:register'))
        self.assertNotContains(page, 'data-password-toggle="id_username"')
        self.assertNotContains(page, 'data-password-toggle="id_email"')
