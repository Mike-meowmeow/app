import uuid

from django.contrib.auth import authenticate, get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from core.models import Department

from .models import PrivacyAcknowledgment

User = get_user_model()


class CustomUserTests(TestCase):
    def test_user_model_is_custom_with_uuid_and_erd_table(self):
        self.assertEqual(User._meta.label, 'accounts.User')
        self.assertEqual(User._meta.db_table, 'users')
        user = User.objects.create_user('applicant1', 'A1@Example.COM', 'pw-Strong-123')
        self.assertIsInstance(user.pk, uuid.UUID)

    def test_email_is_normalized_and_unique_case_insensitively(self):
        user = User.objects.create_user('applicant1', '  Person@Example.COM ', 'pw-Strong-123')
        self.assertEqual(user.email, 'person@example.com')
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user('applicant2', 'PERSON@example.com', 'pw-Strong-123')

    def test_username_is_unique_case_insensitively(self):
        User.objects.create_user('Applicant', 'a@example.com', 'pw-Strong-123')
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create_user('applicant', 'b@example.com', 'pw-Strong-123')

    def test_password_is_hashed_and_login_ignores_username_case(self):
        user = User.objects.create_user('Applicant', 'a@example.com', 'pw-Strong-123')
        self.assertNotEqual(user.password, 'pw-Strong-123')
        self.assertTrue(user.password.startswith('pbkdf2_'))
        self.assertEqual(authenticate(username='applicant', password='pw-Strong-123'), user)

    def test_timestamps_equal_on_insert_and_advance_on_update(self):
        user = User.objects.create_user('applicant1', 'a@example.com', 'pw-Strong-123')
        self.assertEqual(user.created_at, user.updated_at)
        created = user.created_at
        user.first_name = 'Ana'
        user.save(update_fields=['first_name'])
        user.refresh_from_db()
        self.assertEqual(user.created_at, created)
        self.assertGreater(user.updated_at, created)

    def test_middle_name_optional_and_department_protected(self):
        dept = Department.objects.create(name='College of Education', code='COED')
        user = User.objects.create_user('staff1', 's@example.com', 'pw-Strong-123',
                                        department=dept)
        self.assertIsNone(user.middle_name)
        self.assertEqual(user.full_name, '')
        with self.assertRaises(IntegrityError), transaction.atomic():
            dept.delete()


class PrivacyAcknowledgmentTests(TestCase):
    def test_acknowledgment_is_append_only(self):
        user = User.objects.create_user('applicant1', 'a@example.com', 'pw-Strong-123')
        ack = PrivacyAcknowledgment.objects.create(user=user, notice_version='2026-11')
        self.assertEqual(ack.created_at, ack.updated_at)
        ack.notice_version = '2027-01'
        with self.assertRaises(PermissionError):
            ack.save()
        with self.assertRaises(PermissionError):
            ack.delete()
