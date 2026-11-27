"""Breadcrumb navigation for applicant and staff pages."""

import html
import re

from django.urls import URLResolver, get_resolver, reverse

from accounts import roles
from accounts.test_stage3 import make_user
from committee.tests import CommitteeTestCase
from matrix import services as rosters
from matrix.tests import RosterTestCase
from recruitment import application_services as applications
from recruitment.models import Vacancy

from .breadcrumbs import PAGES, build_trail

# URLs that are actions, downloads, redirects, or entry pages without a trail.
NO_TRAIL = {
    'core:home', 'core:dashboard', 'core:workspace_applicant',
    'accounts:login', 'accounts:staff_login', 'accounts:logout', 'accounts:register',
    'accounts:verify_email', 'accounts:resend_verification', 'accounts:password_reset',
    'accounts:password_reset_done', 'accounts:password_reset_confirm',
    'accounts:password_reset_complete', 'accounts:staff_invite',
    'accounts:two_factor_verify', 'accounts:two_factor_setup', 'accounts:two_factor_reset',
    'applicants:delete_record', 'documents:download',
    'applicants:attach_document', 'applicants:document_file', 'applicants:remove_document',
    'recruitment:import_profile_documents',
    'recruitment:apply', 'recruitment:upload_document', 'recruitment:remove_document',
    'recruitment:submit_application', 'recruitment:resubmit_corrections',
    'recruitment:application_start_review', 'recruitment:application_reopen',
    'recruitment:fact_review', 'recruitment:fact_evidence', 'recruitment:fact_unlink',
    'recruitment:position_set_active', 'recruitment:position_item_remove',
    'recruitment:requirements_start', 'recruitment:requirements_discard',
    'recruitment:requirement_remove', 'recruitment:vacancy_publish',
    'recruitment:vacancy_close', 'recruitment:vacancy_cancel', 'recruitment:vacancy_delete',
    'recruitment:assignment_add', 'recruitment:assignment_end',
    'matrix:template_approve', 'matrix:download', 'matrix:regenerate', 'matrix:new_version',
    'matrix:submit_for_approval', 'matrix:approve', 'matrix:return', 'matrix:release',
    'matrix:export_draft',
    'committee:member_add', 'committee:committee_active', 'committee:member_end',
    'committee:interview_cancel', 'committee:panel_add', 'committee:panel_remove',
    'committee:conflict_declare', 'committee:evaluation_discard',
    'notifications:open', 'notifications:mark_all_read', 'notifications:retry',
    'analytics:report_download',
    'ai_processing:run', 'ai_processing:retry', 'ai_processing:item_action',
    'ai_processing:run_checks', 'assistant:ask', 'assistant:run', 'assistant:feedback', 'core:credential_names', 'ai_processing:run_matching', 'ai_processing:retry_matching',
}


def url_names(patterns=None, namespace=''):
    for entry in patterns if patterns is not None else get_resolver().url_patterns:
        if isinstance(entry, URLResolver):
            if entry.namespace != 'admin':
                inner = f'{entry.namespace}:' if entry.namespace else namespace
                yield from url_names(entry.url_patterns, inner)
        elif entry.name:  # unnamed redirects (the admin sign-in) cannot be linked to
            yield namespace + entry.name


class TrailMixin:
    def trail(self, url):
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, url)
        found = re.search(r'<ol class="breadcrumb.*?</ol>', response.content.decode(), re.S)
        self.assertIsNotNone(found, f'no breadcrumb on {url}')
        return [(html.unescape(re.sub(r'<[^>]+>', '', item).strip()),
                 (re.search(r'href="([^"]+)"', item) or [None, None])[1])
                for item in re.findall(r'<li.*?</li>', found.group(0), re.S)]

    def assert_trail(self, url, *labels):
        trail = self.trail(url)
        self.assertEqual([label for label, _ in trail], list(labels))
        self.assertIsNone(trail[-1][1], 'the current page is not a link')
        for label, link in trail[:-1]:
            response = self.client.get(link, follow=True)
            self.assertEqual(response.status_code, 200, (label, link))


class BreadcrumbTests(TrailMixin, CommitteeTestCase):
    def setUp(self):
        super().setUp()
        self.ref = self.application.reference
        self.title = self.vacancy.position_title

    def test_every_page_has_a_trail_or_is_deliberately_excluded(self):
        self.assertEqual(set(url_names()) - set(PAGES) - NO_TRAIL, set())
        self.assertEqual(set(PAGES) & NO_TRAIL, set())

    def test_declared_parents_exist(self):
        for name, page in PAGES.items():
            if page.parent:
                self.assertIn(page.parent, PAGES, name)

    def test_applicant_trails(self):
        self.client.force_login(self.applicant)
        url = reverse('recruitment:my_application', args=[self.application.pk])
        self.assert_trail(url, 'Home', 'My applications', self.ref)
        self.assert_trail(reverse('applicants:add_record', args=['training']),
                          'Home', 'My profile', 'Add training')
        self.assert_trail(reverse('recruitment:public_detail', args=[self.vacancy.pk]),
                          'Home', 'Vacancies', self.title)

    def test_signed_out_home_is_the_public_home(self):
        trail = self.trail(reverse('recruitment:public_detail', args=[self.vacancy.pk]))
        self.assertEqual(trail[0], ('Home', reverse('core:home')))

    def test_hr_trail_through_the_vacancy(self):
        self.client.force_login(self.hr)
        self.assert_trail(reverse('recruitment:request_correction', args=[self.application.pk]),
                          'Home', 'HR', 'Vacancies', self.title, 'Applications', self.ref,
                          'Request correction')

    def test_interview_pages_lead_back_through_selection(self):
        self.shortlist()
        interview = self.schedule()
        self.client.force_login(self.hr)
        self.assert_trail(reverse('committee:interview_reschedule', args=[interview.pk]),
                          'Home', 'HR', 'Vacancies', self.title, 'Applications', self.ref,
                          'Selection', 'Reschedule interview')

    def test_committee_member_trail_avoids_pages_they_cannot_open(self):
        self.shortlist()
        self.client.force_login(self.psb1)
        self.assert_trail(reverse('committee:evaluation_new', args=[self.application.pk]),
                          'Home', 'PSB', 'Assigned candidates', self.ref, 'New evaluation')

    def test_approver_and_catalog_trails(self):
        self.client.force_login(self.approver)
        self.assert_trail(reverse('committee:vacancy_committees', args=[self.vacancy.pk]),
                          'Home', 'Approver', 'Vacancies', self.title,
                          'Committees and interviews')
        self.client.force_login(self.hr)
        position = self.vacancy.position
        self.assert_trail(reverse('recruitment:position_edit', args=[position.pk]),
                          'Home', 'HR', 'Position catalog', position.title, 'Edit position')

    def test_refused_pages_reveal_nothing(self):
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('recruitment:vacancy_detail',
                                           args=[self.vacancy.pk]))
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, 'Breadcrumb', status_code=403)
        self.assertNotContains(response, self.title, status_code=403)

    def test_home_and_workspace_pages_keep_a_trail(self):
        self.assert_trail(reverse('core:home'), 'Home')
        self.client.force_login(self.applicant)
        self.assert_trail(reverse('core:workspace_applicant'), 'Home')
        self.client.force_login(self.hr)
        self.assert_trail(reverse('core:workspace_hr'), 'Home', 'HR')
        self.client.force_login(self.dept_head)
        self.assert_trail(reverse('core:workspace_department'), 'Home', 'Department Head')
        self.assert_trail(reverse('committee:my_candidates'),
                          'Home', 'Department Head', 'Assigned candidates')

    def test_several_roles_follow_the_workspace_opened_last(self):
        person = make_user('multi', roles.HR, roles.APPROVER, roles.SYSTEM_ADMIN)
        self.client.force_login(person)
        multi_home = reverse('core:dashboard')
        self.assert_trail(multi_home, 'Home')
        vacancies = reverse('recruitment:vacancy_list')
        self.assert_trail(vacancies, 'Home', 'HR', 'Vacancies')
        self.client.get(reverse('core:workspace_approver'))
        self.assert_trail(vacancies, 'Home', 'Approver', 'Vacancies')
        self.client.get(reverse('accounts:user_list'))
        self.assert_trail(vacancies, 'Home', 'HR', 'Vacancies')  # System Admin does not cover it
        self.client.get(reverse('accounts:user_list'))  # clicking HR above changed it
        self.assert_trail(reverse('audit:list'), 'Home', 'System Admin', 'Audit trail')

    def test_system_admin_pages_sit_under_their_workspace(self):
        admin = make_user('sysop', roles.SYSTEM_ADMIN)
        self.client.force_login(admin)
        self.assert_trail(reverse('accounts:user_list'), 'Home', 'System Admin')
        self.assert_trail(reverse('accounts:staff_create'),
                          'Home', 'System Admin', 'New staff account')
        self.assert_trail(reverse('operations:overview'),
                          'Home', 'System Admin', 'Backups and restore checks')

    def test_personal_pages_have_no_workspace(self):
        self.client.force_login(self.hr)
        self.assert_trail(reverse('notifications:list'), 'Home', 'Notifications')

    def test_labels_are_escaped_shortened_and_never_applicant_names(self):
        long_title = '<b>Instructor</b> ' + 'x' * 80
        Vacancy.objects.filter(pk=self.vacancy.pk).update(position_title=long_title)
        self.client.force_login(self.hr)
        response = self.client.get(reverse('recruitment:vacancy_edit', args=[self.vacancy.pk]))
        crumbs = re.search(r'<ol class="breadcrumb.*?</ol>', response.content.decode(),
                           re.S).group(0)
        self.assertNotIn('<b>', crumbs)
        self.assertIn('&lt;b&gt;', crumbs)
        self.assertIn('…', crumbs)
        trail = build_trail(self.hr, 'recruitment:application_review',
                            {'pk': self.application.pk})
        self.assertNotIn(self.applicant.last_name, ' '.join(c['label'] for c in trail))

    def test_missing_records_produce_no_trail_instead_of_an_error(self):
        trail = build_trail(self.hr, 'recruitment:vacancy_detail',
                            {'pk': '00000000-0000-0000-0000-000000000000'})
        self.assertEqual(trail, [])


class RosterBreadcrumbTests(TrailMixin, RosterTestCase):
    def test_roster_row_trail(self):
        application = self.submit_as(self.applicant_with('Abad'))
        applications.start_review(self.hr, application)
        self.accept_all(application)
        roster = rosters.generate(self.hr, self.vacancy)
        entry = roster.entries.get()
        self.client.force_login(self.hr)
        self.assert_trail(reverse('matrix:entry_edit', args=[roster.pk, entry.pk]),
                          'Home', 'HR', 'Vacancies', self.vacancy.position_title,
                          'Official roster',
                          f'Version {roster.version}', f'Row {entry.sequence_number}')
