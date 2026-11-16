"""Stage 4 applicant profile tests (FR-004) and applicant record ownership."""

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from accounts import roles, services as account_services
from accounts.test_stage3 import make_user, seed
from audit.models import AuditLog

from .models import Applicant, EducationalRecord, WorkExperience


class ProfileTests(TestCase):
    def setUp(self):
        seed()
        self.ana = make_user('ana', roles.APPLICANT)
        self.ben = make_user('ben', roles.APPLICANT)
        self.client.force_login(self.ana)

    def personal(self, **overrides):
        data = {'first_name': 'Ana', 'middle_name': '', 'last_name': 'Cruz',
                'contact_no': '09171234567', 'birthdate': '1995-04-01', 'sex': 'female',
                'civil_status': 'single', 'address': 'Ormoc City, Leyte'}
        data.update(overrides)
        return self.client.post(reverse('applicants:edit_personal'), data)

    def test_profile_created_on_first_visit_and_lists_missing_information(self):
        response = self.client.get(reverse('applicants:profile'))
        self.assertTrue(Applicant.objects.filter(user=self.ana).exists())
        for item in ('Contact number', 'Birthdate', 'At least one education record'):
            self.assertContains(response, item)

    def test_personal_information_saved_as_text_and_audited(self):
        self.assertRedirects(self.personal(), reverse('applicants:profile'))
        profile = Applicant.objects.get(user=self.ana)
        self.assertEqual(profile.contact_no, '09171234567')  # leading zero kept
        self.ana.refresh_from_db()
        self.assertIsNone(self.ana.middle_name)
        self.assertEqual(self.ana.last_name, 'Cruz')
        event = AuditLog.objects.get(action='profile.updated')
        self.assertEqual(event.actor, self.ana)
        self.assertEqual(event.changes['contact_no'], {'old': '', 'new': '09171234567'})

    def test_invalid_personal_information_is_rejected(self):
        response = self.personal(contact_no='call me', birthdate=str(date.today()
                                                                      + timedelta(days=2)))
        self.assertContains(response, 'Enter a phone number')
        self.assertContains(response, 'cannot be in the future')

    def test_multiple_degrees_and_qualifications(self):
        add = lambda kind, data: self.client.post(  # noqa: E731
            reverse('applicants:add_record', args=[kind]), data)
        add('education', {'degree_level': 'bachelor', 'program': 'BS Mathematics',
                          'institution': 'EVSU', 'completion_status': 'completed',
                          'completed_on': '2016-04-01'})
        add('education', {'degree_level': 'master', 'program': 'MA Mathematics',
                          'institution': 'UP Visayas', 'completion_status': 'ongoing'})
        add('experience', {'position': 'Teacher I', 'organization': 'DepEd',
                           'start_date': '2017-06-01', 'duties': 'Taught math'})
        add('training', {'title': 'Assessment workshop', 'hours': '16'})
        add('eligibility', {'eligibility_type': 'LET', 'rating': '85.20'})
        add('license', {'profession': 'Professional Teacher', 'license_number': '0012345'})
        profile = Applicant.objects.get(user=self.ana)
        self.assertEqual(profile.educational_records.count(), 2)
        self.assertEqual(profile.work_experiences.get().end_date, None)
        self.assertEqual(profile.professional_licenses.get().license_number, '0012345')
        page = self.client.get(reverse('applicants:profile')).content.decode()
        self.assertLess(page.index('BS Mathematics'), page.index('MA Mathematics'))
        self.assertEqual(AuditLog.objects.filter(action__endswith='_added').count(), 6)

    def test_record_rules(self):
        response = self.client.post(reverse('applicants:add_record', args=['education']), {
            'degree_level': 'master', 'program': 'MA', 'institution': 'X',
            'completion_status': 'ongoing', 'completed_on': '2020-01-01'})
        self.assertContains(response, 'Only completed education has a completion date')
        response = self.client.post(reverse('applicants:add_record', args=['experience']), {
            'position': 'P', 'organization': 'O', 'start_date': '2020-01-01',
            'end_date': '2019-01-01'})
        self.assertContains(response, 'cannot be earlier than the start date')
        self.assertFalse(EducationalRecord.objects.exists())
        self.assertFalse(WorkExperience.objects.exists())

    def test_edit_records_with_change_history(self):
        self.client.post(reverse('applicants:add_record', args=['training']),
                         {'title': 'Workshop', 'hours': '8'})
        record = Applicant.objects.get(user=self.ana).trainings.get()
        self.client.post(reverse('applicants:edit_record', args=['training', record.pk]),
                         {'title': 'Workshop', 'hours': '16'})
        event = AuditLog.objects.get(action='profile.training_updated')
        self.assertEqual(event.changes, {'hours': {'old': '8.00', 'new': '16.00'}})

    def test_applicants_cannot_touch_each_others_records(self):
        self.client.force_login(self.ben)
        self.client.post(reverse('applicants:add_record', args=['education']), {
            'degree_level': 'bachelor', 'program': 'BSEd', 'institution': 'EVSU',
            'completion_status': 'completed'})
        bens = Applicant.objects.get(user=self.ben).educational_records.get()
        self.client.force_login(self.ana)
        self.assertEqual(self.client.get(reverse('applicants:edit_record',
                                                 args=['education', bens.pk])).status_code, 404)
        self.assertEqual(self.client.post(reverse('applicants:delete_record',
                                                  args=['education', bens.pk])).status_code, 404)
        self.assertTrue(EducationalRecord.objects.filter(pk=bens.pk).exists())
        self.assertNotContains(self.client.get(reverse('applicants:profile')), 'BSEd')

    def test_staff_have_no_applicant_profile(self):
        hr = make_user('hr1', roles.HR)
        self.client.force_login(hr)
        self.assertEqual(self.client.get(reverse('applicants:profile')).status_code, 403)


class AdminSiteAccessTests(TestCase):
    def test_system_admin_role_controls_admin_site_access(self):
        seed()
        user = make_user('ops')
        account_services.set_roles(user, [roles.SYSTEM_ADMIN], reason='Ops admin')
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        account_services.set_roles(user, [], reason='Left')
        user.refresh_from_db()
        self.assertFalse(user.is_staff)
        self.assertEqual(AuditLog.objects.filter(action='account.admin_site_access',
                                                 entity_id=str(user.pk)).count(), 2)
