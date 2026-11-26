"""The license and eligibility reference list: seed, lookup, the JSON endpoint,
the System Administrator page, suggestions in forms, and the qualification check."""

from datetime import date
from io import StringIO

from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts import roles
from accounts.test_stage3 import make_user, seed
from ai_processing import qualification as rules
from ai_processing.test_qualification import fact, standard
from audit.models import AuditLog
from recruitment.forms import QualificationStandardForm
from recruitment.models import CriterionType

from . import credentials
from .models import Credential, CredentialKind

LICENSE, ELIGIBILITY = CredentialKind.LICENSE, CredentialKind.ELIGIBILITY


class CredentialListTests(TestCase):
    def setUp(self):
        seed()
        call_command('seed_reference_data', stdout=StringIO())

    def test_seed_adds_both_kinds_once_and_keeps_edits(self):
        self.assertGreater(Credential.objects.filter(kind=LICENSE).count(), 40)
        self.assertGreater(Credential.objects.filter(kind=ELIGIBILITY).count(), 5)
        teacher = Credential.objects.get(kind=LICENSE, name='Professional Teacher')
        teacher.aliases = 'Teacher license'
        teacher.save()
        before = Credential.objects.count()
        call_command('seed_reference_data', stdout=StringIO())
        self.assertEqual(Credential.objects.count(), before)
        teacher.refresh_from_db()
        self.assertEqual(teacher.aliases, 'Teacher license')  # not overwritten

    def test_canonical_ignores_case_spacing_and_punctuation(self):
        self.assertEqual(credentials.canonical(LICENSE, 'LET passer'), 'Professional Teacher')
        self.assertEqual(credentials.canonical(LICENSE, '  professional   TEACHER. '),
                         'Professional Teacher')
        self.assertEqual(credentials.canonical(ELIGIBILITY, 'cs professional'),
                         'Career Service Professional')
        self.assertIsNone(credentials.canonical(LICENSE, 'Unlisted Profession'))
        self.assertIsNone(credentials.canonical(LICENSE, 'cs professional'))  # other kind
        Credential.objects.filter(name='Professional Teacher').update(is_active=False)
        self.assertIsNone(credentials.canonical(LICENSE, 'LET'))  # retired names do not match

    def test_json_endpoint(self):
        url = reverse('core:credential_names')
        self.assertEqual(self.client.get(url, {'kind': 'license'}).status_code, 302)  # sign in
        applicant = make_user('appl1', roles.APPLICANT)
        self.client.force_login(applicant)
        self.assertEqual(self.client.get(url, {'kind': 'nothing'}).status_code, 400)
        data = self.client.get(url, {'kind': 'license', 'q': 'teach'}).json()
        self.assertEqual(data['kind'], 'license')
        self.assertIn('Professional Teacher', [r['name'] for r in data['results']])
        data = self.client.get(url, {'kind': 'license', 'q': 'LET'}).json()  # by alias
        self.assertEqual(data['results'][0]['name'], 'Professional Teacher')
        with override_settings(SITE_PORTAL='public'):  # applicants use it on the public site
            self.assertEqual(self.client.get(url, {'kind': 'eligibility'}).status_code, 200)

    def test_only_system_admins_manage_the_list(self):
        url = reverse('core:credential_list')
        hr = make_user('hr1', roles.HR)
        self.client.force_login(hr)
        self.assertEqual(self.client.get(url).status_code, 403)
        admin = make_user('admin1', roles.SYSTEM_ADMIN)
        self.client.force_login(admin)
        self.assertContains(self.client.get(url), 'Professional Teacher')
        response = self.client.post(url, {'kind': 'license', 'name': '  Speech  Pathologist ',
                                          'aliases': 'SLP', 'is_active': 'on'})
        self.assertEqual(response.status_code, 302)
        added = Credential.objects.get(name='Speech Pathologist')
        self.assertEqual(added.alias_list(), ['SLP'])
        self.assertTrue(AuditLog.objects.filter(action='credential.created').exists())
        response = self.client.post(url, {'kind': 'license', 'name': 'Speech Pathologist'})
        self.assertContains(response, 'already in the list')
        edit = reverse('core:credential_edit', args=[added.pk])
        self.client.post(edit, {'kind': 'license', 'name': 'Speech Pathologist', 'aliases': ''})
        added.refresh_from_db()
        self.assertFalse(added.is_active)  # unchecked: retired
        self.assertTrue(AuditLog.objects.filter(action='credential.updated').exists())
        self.assertContains(self.client.get(url), 'retired')

    def test_forms_suggest_the_listed_names(self):
        html = str(QualificationStandardForm()['name'])
        self.assertIn('list="id_name-license-names"', html)
        self.assertIn('<datalist id="id_name-eligibility-names">', html)
        self.assertIn('<option value="Professional Teacher">', html)
        self.assertIn('data-credential-switch="criterion_type"', html)
        self.assertIn('core/credentials.js', html)
        applicant = make_user('appl2', roles.APPLICANT)
        self.client.force_login(applicant)
        page = self.client.get(reverse('applicants:add_record', args=['license']))
        self.assertContains(page, 'list="id_profession-license-names"')
        self.assertContains(page, '<option value="Registered Nurse">')
        page = self.client.get(reverse('applicants:add_record', args=['eligibility']))
        self.assertContains(page, '<option value="Career Service Professional">')

    def test_any_name_can_still_be_typed(self):
        form = QualificationStandardForm({'criterion_type': CriterionType.LICENSE,
                                          'name': 'A license not on the list',
                                          'description': 'Needs a license.'})
        self.assertTrue(form.is_valid(), form.errors)

    def test_qualification_check_matches_other_spellings(self):
        lic = standard(CriterionType.LICENSE, name='Professional Teacher')
        let = fact('license', profession='LET passer', license_number='1',
                   expires_on='2027-01-01')
        outcome = rules.evaluate(lic, [let], date(2026, 6, 30))
        self.assertEqual(outcome.result, rules.MEETS)
        self.assertIn('same credential: "Professional Teacher"', ' '.join(outcome.lines))
        elig = standard(CriterionType.ELIGIBILITY, name='RA 1080')
        board = fact('eligibility', eligibility_type='Board passer')
        self.assertEqual(rules.evaluate(elig, [board], date(2026, 6, 30)).result, rules.MEETS)
        # Without the list entry, the same names no longer match.
        Credential.objects.filter(name='Professional Teacher').update(is_active=False)
        self.assertNotEqual(rules.evaluate(lic, [let], date(2026, 6, 30)).result, rules.MEETS)

    def test_list_changes_make_checks_out_of_date(self):
        first = credentials.digest()
        Credential.objects.filter(name='Professional Teacher').update(aliases='LET\nLPT\nTeacher')
        self.assertNotEqual(credentials.digest(), first)
