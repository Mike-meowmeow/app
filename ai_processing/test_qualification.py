"""Advisory qualification checks (13.4)."""

import uuid
from datetime import date
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from recruitment import application_services as applications
from recruitment.models import (ApplicationStatus, CriterionType, FactReviewStatus, FactType,
                                RelevanceStatus)
from recruitment.test_applications import ApplicationTestCase

from . import qualification as rules
from . import screening, services
from .models import AIJob, AIScreening, JobType

AS_OF = date(2026, 6, 30)
A, P, R = FactReviewStatus.ACCEPTED, FactReviewStatus.PENDING, FactReviewStatus.REJECTED


def fact(fact_type, status=A, relevance=RelevanceStatus.NOT_APPLICABLE, **value):
    return SimpleNamespace(pk=uuid.uuid4(), fact_type=fact_type, value=value,
                           review_status=status, relevance_status=relevance)


def standard(criterion_type, **required):
    return SimpleNamespace(criterion_type=criterion_type, required_value=required)


def degree(level='master', program='MA Mathematics', completion='completed', **kw):
    return fact('education', degree_level=level, program=program,
                completion_status=completion, **kw)


def job(start, end=None, relevance=RelevanceStatus.RELEVANT, status=A):
    return fact('experience', status=status, relevance=relevance, position='Instructor',
                organization='Sample College', start_date=start, end_date=end)


def training(title, hours, start='2024-01-10', relevance=RelevanceStatus.RELEVANT):
    return fact('training', relevance=relevance, title=title, hours=hours, start_date=start)


class RuleTests(TestCase):  # the name check reads the credential list
    def check(self, std, facts):
        return rules.evaluate(std, facts, AS_OF)

    def test_education_level_field_and_completion(self):
        std = standard(CriterionType.EDUCATION, degree_level='master', field='Mathematics')
        self.assertEqual(self.check(std, [degree()]).result, rules.MEETS)
        self.assertEqual(self.check(std, [degree('doctorate', 'PhD Mathematics')]).result,
                         rules.MEETS)  # higher level counts
        self.assertEqual(self.check(std, [degree(program='MA Physics')]).result,
                         rules.NEEDS_REVIEW)  # field: HR decides
        self.assertEqual(self.check(std, [degree(completion='ongoing')]).result,
                         rules.NEEDS_REVIEW)  # incomplete degree is not Meets
        self.assertEqual(self.check(std, [degree(completion='not_completed')]).result,
                         rules.DOES_NOT_MEET)
        self.assertEqual(self.check(std, [degree('bachelor', 'BS Mathematics')]).result,
                         rules.DOES_NOT_MEET)
        self.assertEqual(self.check(std, [degree('other', 'Units in Mathematics')]).result,
                         rules.DOES_NOT_MEET)

    def test_only_accepted_facts_count_and_pending_ones_make_it_uncertain(self):
        std = standard(CriterionType.EDUCATION, degree_level='master')
        self.assertEqual(self.check(std, []).result, rules.DOES_NOT_MEET)
        self.assertEqual(self.check(std, [degree(status=R)]).result, rules.DOES_NOT_MEET)
        pending = self.check(std, [degree(status=P)])
        self.assertEqual(pending.result, rules.NEEDS_REVIEW)
        self.assertIn('not yet reviewed', pending.explanation)
        self.assertEqual(pending.evidence, [])

    def test_experience_overlap_is_counted_once(self):
        std = standard(CriterionType.EXPERIENCE, amount='2')
        # Two jobs over the same 18 months: 1.5 years, not 3.
        overlap = self.check(std, [job('2020-01-01', '2021-06-30'),
                                   job('2020-01-01', '2021-06-30')])
        self.assertEqual(overlap.result, rules.DOES_NOT_MEET)
        self.assertIn('counted once', overlap.explanation)
        self.assertIn('1.5 years', overlap.explanation)
        separate = self.check(std, [job('2018-01-01', '2018-12-31'),
                                    job('2020-01-01', '2020-12-31')])
        self.assertEqual(separate.result, rules.MEETS)

    def test_ongoing_experience_counts_to_the_submission_date(self):
        std = standard(CriterionType.EXPERIENCE, amount='2')
        self.assertEqual(self.check(std, [job('2024-07-01')]).result, rules.MEETS)
        self.assertEqual(self.check(std, [job('2025-01-01')]).result, rules.DOES_NOT_MEET)

    def test_experience_needs_relevance_and_reports_unknowns(self):
        std = standard(CriterionType.EXPERIENCE, amount='1')
        unassessed = self.check(std, [job('2010-01-01', '2015-01-01',
                                          relevance=RelevanceStatus.NOT_ASSESSED)])
        self.assertEqual(unassessed.result, rules.NEEDS_REVIEW)
        not_relevant = self.check(std, [job('2010-01-01', '2015-01-01',
                                            relevance=RelevanceStatus.NOT_RELEVANT)])
        self.assertEqual(not_relevant.result, rules.DOES_NOT_MEET)
        no_start = self.check(std, [job(None, '2015-01-01')])
        self.assertEqual(no_start.result, rules.NEEDS_REVIEW)
        self.assertIn('no start date', no_start.explanation)

    def test_training_hours_duplicates_and_unknown_hours(self):
        std = standard(CriterionType.TRAINING, amount='16')
        self.assertEqual(self.check(std, [training('Assessment', '8'),
                                          training('Assessment', '8')]).result,
                         rules.DOES_NOT_MEET)  # the same training twice counts once
        self.assertEqual(self.check(std, [training('Assessment', '8'),
                                          training('Assessment', '8', start='2025-02-01')]).result,
                         rules.MEETS)  # same title, different dates: two trainings
        unknown = self.check(std, [training('Assessment', '8'), training('Research', None)])
        self.assertEqual(unknown.result, rules.NEEDS_REVIEW)
        self.assertIn('hours not recorded', unknown.explanation)

    def test_zero_minimum_is_not_required(self):
        self.assertEqual(self.check(standard(CriterionType.TRAINING, amount='0'), []).result,
                         rules.NOT_REQUIRED)

    def test_eligibility_and_license_matching(self):
        elig = standard(CriterionType.ELIGIBILITY, name='Civil Service Professional')
        self.assertEqual(self.check(elig, [fact('eligibility',
                                                eligibility_type='Career Service Professional '
                                                                 '(Civil Service)')]).result,
                         rules.MEETS)
        self.assertEqual(self.check(elig, [fact('eligibility',
                                                eligibility_type='Civil Service Sub-Professional')]
                                    ).result, rules.NEEDS_REVIEW)
        lic = standard(CriterionType.LICENSE, name='Professional Teacher')
        valid = fact('license', profession='Professional Teacher', license_number='0000001',
                     expires_on='2027-01-01')
        expired = fact('license', profession='Professional Teacher', license_number='0000002',
                       expires_on='2025-01-01')
        self.assertEqual(self.check(lic, [valid]).result, rules.MEETS)
        result = self.check(lic, [expired])
        self.assertEqual(result.result, rules.NEEDS_REVIEW)
        self.assertIn('expired', result.explanation)
        self.assertEqual(self.check(lic, [fact('license', profession='Engineer',
                                               license_number='1')]).result, rules.DOES_NOT_MEET)

    def test_other_standards_are_for_hr(self):
        self.assertEqual(self.check(standard(CriterionType.OTHER), [degree()]).result,
                         rules.NEEDS_REVIEW)

    def test_overall(self):
        self.assertEqual(rules.overall([rules.MEETS, rules.NEEDS_REVIEW]), rules.NEEDS_REVIEW)
        self.assertEqual(rules.overall([rules.MEETS, rules.DOES_NOT_MEET, rules.NEEDS_REVIEW]),
                         rules.DOES_NOT_MEET)
        self.assertEqual(rules.overall([rules.MEETS, rules.NOT_REQUIRED]), rules.MEETS)


class ScreeningTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.education = self.application.facts.get(fact_type=FactType.EDUCATION)
        self.open_review(self.application)  # the review opens only when everything is decided
        self.education.refresh_from_db()
        self.application.refresh_from_db()

    def accept_education(self):
        """Already accepted by open_review (kept so the tests read naturally)."""
        self.education.refresh_from_db()
        self.assertEqual(self.education.review_status, FactReviewStatus.ACCEPTED)

    def change_inputs(self):
        """New information after a check: HR adds a fact found in a document."""
        return applications.add_manual_fact(
            self.hr, self.application, 'training',
            {'title': 'Outcome-based teaching', 'hours': '8', 'provider': 'CHED',
             'start_date': None, 'end_date': None}, reason='Certificate in the PDS')

    def test_check_uses_reviewed_facts_and_changes_nothing(self):
        revision, status = self.application.revision, self.application.status
        first, created = screening.run_checks(self.hr, self.application)
        self.assertTrue(created)
        result = first.results.get()
        self.assertEqual(result.result, rules.MEETS)
        self.assertEqual(result.evidence_fact_ids, [str(self.education.pk)])
        self.application.refresh_from_db()
        self.assertEqual((self.application.revision, self.application.status), (revision, status))
        self.assertFalse(self.application.facts.filter(
            fact_type=FactType.QUALIFICATION_ASSESSMENT).exists())

        self.change_inputs()
        self.assertFalse(screening.is_current(first))
        second, created = screening.run_checks(self.hr, self.application)
        self.assertTrue(created)
        first.job.refresh_from_db()
        self.assertTrue(first.job.is_invalidated)
        self.assertEqual(screening.latest(self.application), second)

    def test_repeating_with_unchanged_inputs_returns_the_stored_check(self):
        first, _ = screening.run_checks(self.hr, self.application)
        again, created = screening.run_checks(self.hr, self.application)
        self.assertFalse(created)
        self.assertEqual(again, first)
        self.assertEqual(AIScreening.objects.count(), 1)

    def test_job_records_rules_and_inputs_without_identity(self):
        check, _ = screening.run_checks(self.hr, self.application)
        job = check.job
        self.assertEqual((job.job_type, job.model_version),
                         (JobType.QUALIFICATION_CHECK, rules.RULES_VERSION))
        self.assertEqual({f['fact_type'] for f in job.input_manifest['facts']}, {'education'})
        self.assertNotIn('Santos', str(job.input_manifest))
        self.assertIsNone(services.claim_next('test'))  # the worker never takes checks

    def test_who_can_run(self):
        with self.assertRaises(PermissionDenied):
            screening.run_checks(self.applicant, self.application)
        self.client.force_login(self.approver)
        self.client.post(reverse('ai_processing:run_checks', args=[self.application.pk]))
        self.assertFalse(AIJob.objects.exists())

    def test_drafts_and_switched_off(self):
        draft = self.draft(self.other_applicant)
        self.assertTrue(screening.problems(draft))
        with self.assertRaises(PermissionDenied):  # HR does not review drafts at all
            screening.run_checks(self.hr, draft)
        with override_settings(AI_QUALIFICATION_CHECKS_ENABLED=False):
            with self.assertRaises(ValidationError):
                screening.run_checks(self.hr, self.application)
            self.client.force_login(self.hr)
            page = self.client.get(reverse('recruitment:application_review',
                                           args=[self.application.pk]))
            self.assertNotContains(page, 'Run qualification checks')

    def test_pages(self):
        self.accept_education()
        self.client.force_login(self.hr)
        review = reverse('recruitment:application_review', args=[self.application.pk])
        self.assertContains(self.client.get(review), 'Run qualification checks')
        response = self.client.post(reverse('ai_processing:run_checks',
                                            args=[self.application.pk]))
        check = AIScreening.objects.get()
        # Stays on the review page; the reasoning opens inside the card.
        self.assertRedirects(response, review + '?checked=1#qualifications',
                             fetch_redirect_response=False)
        page = self.client.get(review + '?checked=1')
        self.assertContains(page, 'Check suggests')
        self.assertContains(page, 'Meets Requirement')
        self.assertContains(page, 'program matches')
        self.assertContains(page, f'class="collapse show" id="check-{check.results.get().standard_id}"')
        self.assertContains(page, 'aria-expanded="true"')
        self.assertContains(page, 'MA Mathematics')  # facts used
        self.assertContains(page, '?check=')  # record outcome starting from this check
        self.assertNotContains(page, 'Details and reasoning')
        # The details page still opens for old links.
        detail = reverse('ai_processing:screening', args=[self.application.pk, check.pk])
        self.assertContains(self.client.get(detail), 'Advisory')

    def test_review_page_opens_the_reasoning_only_after_a_run(self):
        self.accept_education()
        check, _ = screening.run_checks(self.hr, self.application)
        standard_id = check.results.get().standard_id
        self.client.force_login(self.hr)
        review = reverse('recruitment:application_review', args=[self.application.pk])
        page = self.client.get(review)
        self.assertContains(page, f'class="collapse" id="check-{standard_id}"')
        self.assertContains(page, 'aria-expanded="false"')
        self.assertContains(page, 'program matches')  # present, folded
        self.assertContains(page, 'Show reasoning')

    def test_out_of_date_check_hides_record_from_check(self):
        screening.run_checks(self.hr, self.application)
        self.change_inputs()  # inputs changed after the check
        self.client.force_login(self.hr)
        page = self.client.get(reverse('recruitment:application_review',
                                       args=[self.application.pk]) + '?checked=1')
        self.assertContains(page, 'Out of date')
        self.assertNotContains(page, '?check=')

    def test_hr_can_start_the_official_outcome_from_the_check(self):
        self.accept_education()
        check, _ = screening.run_checks(self.hr, self.application)
        result = check.results.get()
        url = reverse('recruitment:qualification_outcome',
                      args=[self.application.pk, result.standard_id]) + f'?check={result.pk}'
        self.client.force_login(self.hr)
        self.assertContains(self.client.get(url), 'Filled in from the advisory check')
        self.client.post(url, {'outcome': 'meets', 'reason': 'Checked the TOR.',
                               'supporting_facts': [str(self.education.pk)]})
        official = applications.qualification_outcomes(self.application)[str(result.standard_id)]
        self.assertEqual(official.value['advisory_check']['result'], rules.MEETS)
        self.assertEqual(official.reviewed_by, self.hr)

    def test_out_of_date_check_does_not_fill_the_form(self):
        check, _ = screening.run_checks(self.hr, self.application)
        result = check.results.get()
        self.change_inputs()  # inputs changed
        self.assertIsNone(screening.usable_result(self.application, result.standard,
                                                  str(result.pk)))
        self.assertIsNone(screening.usable_result(self.application, result.standard,
                                                  'not-a-uuid'))

    def test_withdrawn_applications_are_not_checked(self):
        applications.withdraw(self.applicant, self.application, reason='Other plans')
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.WITHDRAWN)
        self.assertTrue(screening.problems(self.application))
