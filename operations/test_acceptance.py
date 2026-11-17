"""Stage 9 non-AI acceptance scenario (Development Guide section 12), as one
automated walk-through on MySQL:

1. Applicant registers, verifies email, completes qualifications, and submits.
2. HR verifies files, requests corrections, and reviews facts manually.
3. The official roster includes every submitted applicant for the vacancy.
4. HR prepares and a permitted approver releases the Excel/PDF outputs.
5. A committee interviews and records assessments; an authorized person records
   the decision and appointment.
6. Notifications, metrics, history, and downloads work.
7. The same scenario runs with no AI package, queue, or model installed.

This automated run does not replace the HR walkthrough the guide requires.
"""

import importlib.util
import re
from datetime import timedelta

from django.conf import settings
from django.core import mail
from django.urls import reverse
from django.utils import timezone

from accounts.models import User
from accounts.test_stage3 import PASSWORD
from analytics import metrics
from analytics import services as analytics
from committee import services as committee
from committee.models import Interview
from documents.models import DocumentVersion
from matrix import services as rosters
from matrix.models import RosterStatus
from matrix.tests import RosterTestCase
from notifications.models import Notification
from recruitment import application_services as applications
from recruitment import decision_services as decisions
from recruitment.models import Application, ApplicationStatus, DecisionOutcome, DecisionType
from recruitment.test_applications import pdf

S, T, O = ApplicationStatus, DecisionType, DecisionOutcome


class NonAIAcceptanceScenario(RosterTestCase):
    def test_complete_manual_workflow(self):
        c = self.client

        # 1. Registration, email verification, profile, submission (through the screens).
        response = c.post(reverse('accounts:register'), {
            'username': 'carla', 'email': 'carla@example.com', 'first_name': 'Carla',
            'middle_name': '', 'last_name': 'Reyes', 'password1': PASSWORD,
            'password2': PASSWORD, 'privacy_acknowledged': 'on'})
        self.assertContains(response, 'Check your email')
        link = re.search(r'http://\S+/accounts/verify/\S+/', mail.outbox[-1].body).group(0)
        c.get(link[link.index('/accounts/'):])
        carla = User.objects.get(username='carla')
        self.assertIsNotNone(carla.email_verified_at)
        self.assertTrue(c.login(username='carla', password=PASSWORD))
        self.complete_profile(carla)
        c.post(reverse('recruitment:apply', args=[self.vacancy.pk]))
        application = Application.objects.get(applicant__user=carla)
        c.post(reverse('recruitment:upload_document', args=[application.pk]),
               {'document_type': self.pds.pk, 'file': pdf('pds-v1.pdf')})
        c.post(reverse('recruitment:submit_application', args=[application.pk]),
               {'confirm': 'on'})
        application.refresh_from_db()
        self.assertEqual(application.status, S.SUBMITTED)
        other = self.submit_as(self.applicant_with('Abad'))  # a second applicant

        # 2. HR verification, correction, replacement, manual fact review.
        c.force_login(self.hr)
        c.post(reverse('recruitment:application_start_review', args=[application.pk]))
        first = DocumentVersion.objects.get(document__application=application)
        c.post(reverse('recruitment:verify_document', args=[application.pk, first.pk]),
               {'status': 'incomplete', 'notes': 'Page 2 missing'})
        applications.request_correction(self.hr, application, message='Upload all pages.',
                                        document_types=[self.pds],
                                        deadline_at=timezone.now() + timedelta(days=3))
        c.force_login(carla)
        c.post(reverse('recruitment:upload_document', args=[application.pk]),
               {'document_type': self.pds.pk, 'file': pdf('pds-v2.pdf'),
                'replaces': first.document_id})
        c.post(reverse('recruitment:resubmit_corrections', args=[application.pk]))
        application.refresh_from_db()
        self.assertEqual(application.status, S.UNDER_REVIEW)
        self.accept_all(application)
        applications.start_review(self.hr, other)
        self.accept_all(other)
        versions = DocumentVersion.objects.filter(document__application=application)
        self.assertEqual(versions.count(), 2)  # the original file is kept

        # 3-4. Official roster: all submitted applicants; approval and release.
        roster = rosters.generate(self.hr, self.vacancy)
        self.assertEqual(set(roster.entries.values_list('application_id', flat=True)),
                         {application.pk, other.pk})
        self.acknowledge_all(roster)
        self.assertEqual(rosters.submit_for_approval(self.hr, roster), [])
        self.assertEqual(rosters.approve(self.approver, roster), [])
        rosters.release(self.approver, roster)
        roster.refresh_from_db()
        self.assertEqual(roster.status, RosterStatus.APPROVED)
        official = roster.exports.filter(is_draft=False)
        self.assertEqual(official.count(), 2)
        c.force_login(self.approver)
        for export in official:
            response = c.get(reverse('matrix:download', args=[export.pk]))
            self.assertEqual(response.status_code, 200)

        # 5. Committee, interview, evaluation, decisions, appointment.
        psb = self.make_psb()
        board = committee.create_committee(self.hr, self.vacancy, name='PSB')
        chair = committee.add_member(self.hr, board, psb, member_role='chair')
        decisions.release_decision(self.hr, decisions.record_decision(
            self.hr, application, decision_type=T.SHORTLISTING, outcome=O.SHORTLISTED,
            reason='Meets standards'))
        start = timezone.now() + timedelta(days=1)
        interview = committee.schedule_interview(
            self.hr, application, panel=[chair], starts_at=start,
            ends_at=start + timedelta(hours=1), mode='in_person', location='HR Office')
        past = timezone.now() - timedelta(hours=2)
        Interview.objects.filter(pk=interview.pk).update(starts_at=past,
                                                         ends_at=past + timedelta(hours=1))
        committee.record_attendance(self.hr, interview, attendance='attended')
        evaluation = committee.start_evaluation(psb, application, interview=interview,
                                                findings='Clear teaching demonstration.')
        committee.submit_evaluation(psb, evaluation)
        application.refresh_from_db()
        decisions.record_decision(self.hr, application,
                                  decision_type=T.COMMITTEE_RECOMMENDATION,
                                  outcome=O.RECOMMENDED, reason='Unanimous', committee=board)
        application.refresh_from_db()
        self.assertEqual(application.status, S.RECOMMENDED)  # not hired yet
        decisions.record_decision(self.approver, application, decision_type=T.FINAL_OUTCOME,
                                  outcome=O.SELECTED, reason='Approved by the appointing officer')
        application.refresh_from_db()
        appointment = decisions.record_decision(
            self.approver, application, decision_type=T.APPOINTMENT, outcome=O.APPOINTED,
            reason='Appointment issued', appointment_reference='APPT-2026-007',
            appointment_at=timezone.now())
        decisions.release_decision(self.approver, appointment, note='Welcome.')
        application.refresh_from_db()
        self.assertEqual(application.status, S.HIRED)
        self.assertEqual(decisions.applicant_status(application), S.HIRED)

        # 6. Notifications, metrics, history, downloads.
        titles = set(Notification.objects.filter(user=carla).values_list('title', flat=True))
        for expected in ('Application submitted', 'Corrections requested', 'Interview invitation'):
            self.assertIn(expected, titles)
        self.assertTrue(any(t.startswith('Update on your application') for t in titles))
        values = metrics.compute(analytics.scope_vacancies(self.hr, vacancy=self.vacancy))
        self.assertEqual((values['submitted_applications'], values['shortlisted_count'],
                          values['completed_interviews'], values['hired_count']), (2, 1, 1, 1))
        statuses = list(application.workflow_logs.values_list('to_status', flat=True))
        for status in (S.DRAFT, S.SUBMITTED, S.UNDER_REVIEW, S.CORRECTION_REQUESTED,
                       S.SHORTLISTED, S.FOR_INTERVIEW, S.RECOMMENDED, S.HIRED):
            self.assertIn(status, statuses)
        c.force_login(carla)
        response = c.get(reverse('recruitment:my_application', args=[application.pk]))
        self.assertContains(response, 'APPT-2026-007')
        response = c.get(reverse('documents:download', args=[first.pk]))
        self.assertEqual(response.status_code, 200)

        # 7. No AI component is installed or needed.
        self.assertNotIn('ai_processing', ' '.join(settings.INSTALLED_APPS))
        for module in ('ai_processing', 'spacy', 'sentence_transformers', 'celery'):
            self.assertIsNone(importlib.util.find_spec(module), module)

    def make_psb(self):
        from accounts import roles
        from accounts.test_stage3 import make_user
        return make_user('psbchair', roles.PSB_MEMBER)
