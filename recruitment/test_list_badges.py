"""Vacancy list badges and the applicant's status on a vacancy page.

- Applicants see Applied / Draft / Withdrawn on the open vacancies list.
- Staff see each vacancy's submitted applications on the vacancy list,
  counted like the vacancy page's "Applications (N)" button.
- The vacancy page shows the applicant's released status, never an
  unreleased internal decision.
"""

from django.urls import reverse

from accounts import roles
from accounts.test_stage3 import make_user
from committee.tests import CommitteeTestCase

from . import application_services as applications
from .models import DecisionOutcome, DecisionType
from .test_applications import ApplicationTestCase

PUBLIC_LIST = reverse('recruitment:public_list')


class ApplicantBadgeTests(ApplicationTestCase):
    def test_no_badge_before_applying_or_for_visitors(self):
        self.client.force_login(self.applicant)
        self.assertNotContains(self.client.get(PUBLIC_LIST), '>Applied<')
        self.client.logout()
        self.draft()
        self.assertNotContains(self.client.get(PUBLIC_LIST), '>Draft<')

    def test_draft_then_applied_then_withdrawn(self):
        self.client.force_login(self.applicant)
        self.draft()
        self.assertContains(self.client.get(PUBLIC_LIST), '>Draft<')
        application = self.submitted()  # submits the same draft
        page = self.client.get(PUBLIC_LIST)
        self.assertContains(page, '>Applied<')
        self.assertNotContains(page, '>Draft<')
        applications.withdraw(self.applicant, application, reason='Found another job')
        self.assertContains(self.client.get(PUBLIC_LIST), '>Withdrawn<')

    def test_badges_are_per_applicant(self):
        self.submitted()
        self.client.force_login(self.other_applicant)
        self.assertNotContains(self.client.get(PUBLIC_LIST), '>Applied<')


class StaffCountTests(ApplicationTestCase):
    def test_count_matches_submitted_applications(self):
        self.complete_profile(self.other_applicant)
        self.submitted()
        self.draft(self.other_applicant)  # drafts are never counted
        self.client.force_login(self.hr)
        page = self.client.get(reverse('recruitment:vacancy_list'))
        self.assertContains(page, '1 applicant<')
        self.assertContains(page, reverse('recruitment:vacancy_applications',
                                          args=[self.vacancy.pk]))

    def test_draft_vacancy_shows_no_count(self):
        self.new_vacancy(position_title='Part-Time Instructor - Physics')
        self.client.force_login(self.hr)
        page = self.client.get(reverse('recruitment:vacancy_list'))
        self.assertContains(page, '0 applicants<', count=1)  # only the published one


class VacancyPageStatusTests(CommitteeTestCase):
    def test_unreleased_decision_stays_hidden(self):
        self.decide(DecisionType.SHORTLISTING, DecisionOutcome.NOT_SHORTLISTED)
        self.client.force_login(self.application.applicant.user)
        page = self.client.get(reverse('recruitment:public_detail', args=[self.vacancy.pk]))
        self.assertContains(page, 'You have an application for this vacancy (Under Review)')
        self.assertNotContains(page, 'Not Selected')
