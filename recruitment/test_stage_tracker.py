"""Vertical stage tracker on HR's application review page."""

from django.urls import reverse
from django.utils import timezone

from . import application_services as services
from . import stage_tracker
from .models import ApplicationStatus as S, DecisionOutcome, DecisionType, RecruitmentDecision
from .test_applications import ApplicationTestCase


class StageTrackerTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()

    def move(self, *statuses):
        for status in statuses:
            services.transition(self.application, status, actor=self.hr)

    def states(self):
        return {s['key']: (s['state'], s['note']) for s in stage_tracker.build(self.application)}

    def test_no_draft_step_and_submitted_is_current(self):
        steps = stage_tracker.build(self.application)
        self.assertEqual([s['key'] for s in steps], ['submitted', 'under_review', 'shortlisted',
                                                     'interview', 'recommended', 'hired'])
        self.assertEqual(steps[0]['state'], 'current')
        self.assertTrue(all(s['state'] == 'upcoming' and not s['note'] for s in steps[1:]))

    def test_finished_steps_are_done_and_later_ones_upcoming(self):
        self.move(S.UNDER_REVIEW, S.SHORTLISTED, S.FOR_INTERVIEW)
        st = self.states()
        self.assertEqual(st['submitted'][0], 'done')
        self.assertEqual(st['under_review'][0], 'done')
        self.assertEqual(st['shortlisted'][0], 'done')
        self.assertEqual(st['interview'][0], 'current')
        self.assertEqual(st['recommended'][0], 'upcoming')
        self.assertEqual(st['hired'][0], 'upcoming')

    def test_correction_request_is_a_note_on_under_review(self):
        self.move(S.UNDER_REVIEW, S.CORRECTION_REQUESTED)
        self.assertEqual(self.states()['under_review'], ('current', 'Correction requested'))

    def test_document_only_review_skips_the_interview(self):
        self.move(S.UNDER_REVIEW, S.SHORTLISTED, S.RECOMMENDED)
        st = self.states()
        self.assertEqual(st['interview'], ('skipped', 'Skipped'))
        self.assertEqual(st['recommended'][0], 'current')

    def test_not_shortlisted_stops_at_under_review(self):
        self.move(S.UNDER_REVIEW, S.NOT_SELECTED)
        st = self.states()
        self.assertEqual(st['submitted'][0], 'done')
        self.assertEqual(st['under_review'], ('ended', 'Not shortlisted'))
        for key in ('shortlisted', 'interview', 'recommended', 'hired'):
            self.assertEqual(st[key], ('upcoming', 'Not reached'))

    def test_not_selected_after_interview_stops_at_interview(self):
        self.move(S.UNDER_REVIEW, S.SHORTLISTED, S.FOR_INTERVIEW, S.NOT_SELECTED)
        st = self.states()
        self.assertEqual(st['shortlisted'][0], 'done')
        self.assertEqual(st['interview'], ('ended', 'Not Selected'))
        self.assertEqual(st['recommended'], ('upcoming', 'Not reached'))

    def test_withdrawn_and_closed_stop_where_they_were(self):
        self.move(S.WITHDRAWN)
        self.assertEqual(self.states()['submitted'], ('ended', 'Withdrawn'))
        self.complete_profile(self.other_applicant)
        other = self.submitted(self.other_applicant)
        services.transition(other, S.UNDER_REVIEW, actor=self.hr)
        services.transition(other, S.SHORTLISTED, actor=self.hr)
        services.transition(other, S.CLOSED_WITHOUT_SELECTION, actor=self.hr)
        st = {s['key']: s['state'] for s in stage_tracker.build(other)}
        self.assertEqual(st['shortlisted'], 'ended')
        self.assertEqual(st['under_review'], 'done')

    def test_reopened_application_is_current_again(self):
        self.move(S.UNDER_REVIEW, S.NOT_SELECTED, S.UNDER_REVIEW)
        st = self.states()
        self.assertEqual(st['under_review'], ('current', ''))
        self.assertEqual(st['shortlisted'], ('upcoming', ''))

    def test_hired_finishes_every_step(self):
        self.move(S.UNDER_REVIEW, S.SHORTLISTED, S.FOR_INTERVIEW, S.RECOMMENDED, S.HIRED)
        st = self.states()
        self.assertTrue(all(st[k][0] == 'done' for k in
                            ('submitted', 'under_review', 'shortlisted', 'interview',
                             'recommended')))
        self.assertEqual(st['hired'], ('complete', 'Appointment recorded'))

    def test_selected_outcome_is_noted_while_waiting_for_appointment(self):
        self.move(S.UNDER_REVIEW, S.SHORTLISTED, S.RECOMMENDED)
        RecruitmentDecision.objects.create(
            application=self.application, decision_type=DecisionType.FINAL_OUTCOME,
            outcome=DecisionOutcome.SELECTED, reason='Best fit', decided_by=self.hr,
            decision_at=timezone.now())
        self.assertEqual(self.states()['recommended'],
                         ('current', 'Selected; waiting for appointment'))

    def test_review_page_shows_the_tracker(self):
        self.move(S.UNDER_REVIEW)
        self.client.force_login(self.hr)
        response = self.client.get(reverse('recruitment:application_review',
                                           args=[self.application.pk]))
        self.assertContains(response, 'Application progress')
        self.assertContains(response, 'class="st-current" aria-current="step"')
        self.assertContains(response, 'class="st-done"')

    def test_applicant_page_does_not_show_it(self):
        self.client.force_login(self.applicant)
        response = self.client.get(reverse('recruitment:my_application',
                                           args=[self.application.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Application progress')
