"""Locked review actions: a fact or document decision is recorded once; a
mistake is fixed with an explicit change that needs a reason."""

from django.core.exceptions import ValidationError
from django.urls import reverse

from audit.models import AuditLog
from documents import services as documents
from documents.models import DocumentVersion
from notifications.models import Notification

from . import application_services as services
from .models import FactReviewStatus
from .test_applications import ApplicationTestCase


class FactDecisionLockTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.education = self.application.facts.get(fact_type='education')
        self.review_url = reverse('recruitment:application_review', args=[self.application.pk])
        self.fact_url = reverse('recruitment:fact_review',
                                args=[self.application.pk, self.education.pk])

    def test_a_decided_fact_cannot_be_decided_again(self):
        services.review_fact(self.hr, self.education, decision=FactReviewStatus.ACCEPTED)
        for decision in (FactReviewStatus.ACCEPTED, FactReviewStatus.REJECTED):
            with self.assertRaises(ValidationError):
                services.review_fact(self.hr, self.education, decision=decision, reason='x')
        self.education.refresh_from_db()
        self.assertEqual(self.education.review_status, FactReviewStatus.ACCEPTED)

    def test_change_decision_needs_a_reason_and_is_audited(self):
        services.review_fact(self.hr, self.education, decision=FactReviewStatus.ACCEPTED)
        with self.assertRaises(ValidationError):
            services.change_fact_decision(self.hr, self.education,
                                          decision=FactReviewStatus.REJECTED, reason='  ')
        with self.assertRaises(ValidationError):  # same decision
            services.change_fact_decision(self.hr, self.education,
                                          decision=FactReviewStatus.ACCEPTED, reason='x')
        services.change_fact_decision(self.hr, self.education, decision=FactReviewStatus.REJECTED,
                                      reason='Wrong diploma was matched')
        self.education.refresh_from_db()
        self.assertEqual(self.education.review_status, FactReviewStatus.REJECTED)
        self.assertEqual(self.education.review_reason, 'Wrong diploma was matched')
        log = AuditLog.objects.get(action='fact.decision_changed')
        self.assertEqual(log.changes['review_status'], {'old': 'accepted', 'new': 'rejected'})
        # And back again, with a reason.
        services.change_fact_decision(self.hr, self.education, decision=FactReviewStatus.ACCEPTED,
                                      reason='Checked again: it is correct')
        self.education.refresh_from_db()
        self.assertEqual(self.education.review_status, FactReviewStatus.ACCEPTED)

    def test_pending_facts_cannot_be_changed_and_others_cannot_change(self):
        with self.assertRaises(ValidationError):
            services.change_fact_decision(self.hr, self.education,
                                          decision=FactReviewStatus.REJECTED, reason='x')
        services.review_fact(self.hr, self.education, decision=FactReviewStatus.ACCEPTED)
        with self.assertRaises(Exception):  # not assigned to the vacancy
            services.change_fact_decision(self.hr2, self.education,
                                          decision=FactReviewStatus.REJECTED, reason='x')

    def test_review_page_locks_the_buttons_and_offers_change_decision(self):
        self.client.force_login(self.hr)
        page = self.client.get(self.review_url).content.decode()
        self.assertIn('>Accept</button>', page)
        self.assertNotIn('Change decision', page)
        self.client.post(self.fact_url, {'decision': 'accepted'})
        page = self.client.get(self.review_url).content.decode()
        self.assertIn('disabled>&#10003; Accepted</button>', page)
        self.assertIn('Change decision', page)
        self.assertIn('Change to Rejected', page)
        # A second Accept/Reject post (an old page) is refused.
        response = self.client.post(self.fact_url, {'decision': 'rejected', 'reason': 'x'},
                                    follow=True)
        self.assertContains(response, 'already accepted')
        self.education.refresh_from_db()
        self.assertEqual(self.education.review_status, FactReviewStatus.ACCEPTED)
        # Change decision through the page.
        response = self.client.post(self.fact_url, {'change': '1', 'decision': 'rejected',
                                                    'reason': 'Mistake'}, follow=True)
        self.assertContains(response, 'Decision changed to Rejected')
        self.assertContains(response, 'Change to Accepted')

    def test_relevance_shows_the_chosen_value(self):
        experience = services.add_manual_fact(
            self.hr, self.application, 'training',
            {'title': 'Teaching Workshop', 'provider': 'Sample Office', 'hours': '8',
             'start_date': None, 'end_date': None}, reason='From the certificate.')
        services.set_relevance(self.hr, experience, 'relevant')  # first marking: no reason
        with self.assertRaises(ValidationError):
            services.set_relevance(self.hr, experience, 'relevant')
        with self.assertRaises(ValidationError):  # a change needs a reason
            services.set_relevance(self.hr, experience, 'not_relevant')
        self.client.force_login(self.hr)
        page = self.client.get(self.review_url)
        self.assertContains(page, 'disabled>&#10003; Relevant</button>')
        self.assertContains(page, 'Change to Not relevant')
        # Labelled, so it is not confused with the evidence link; only on
        # experience and training facts (the education fact has none).
        self.assertContains(page, 'Relevant to this vacancy?', count=1)
        url = reverse('recruitment:fact_review', args=[self.application.pk, experience.pk])
        self.client.post(url, {'relevance': 'not_relevant', 'reason': 'Unrelated subject'})
        experience.refresh_from_db()
        self.assertEqual(experience.relevance_status, 'not_relevant')
        self.assertTrue(AuditLog.objects.filter(action='fact.relevance_changed').exists())
        self.assertTrue(Notification.objects.filter(user=self.approver,
                                                    title='Review decision changed').exists())

    def test_approvers_are_told_when_a_decision_changes(self):
        services.review_fact(self.hr, self.education, decision=FactReviewStatus.ACCEPTED)
        self.assertFalse(Notification.objects.filter(title='Review decision changed').exists())
        services.change_fact_decision(self.hr, self.education, decision=FactReviewStatus.REJECTED,
                                      reason='Private note about the diploma')
        [note] = Notification.objects.filter(title='Review decision changed')
        self.assertEqual(note.user, self.approver)
        self.assertIn(self.application.reference, note.message)
        self.assertIn('from Accepted to Rejected', note.message)
        self.assertNotIn('Private note', note.message)  # the reason stays on the application
        self.assertFalse(Notification.objects.filter(user=self.hr,
                                                     title='Review decision changed').exists())


class VerificationLockTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.version = DocumentVersion.objects.get()
        self.review_url = reverse('recruitment:application_review', args=[self.application.pk])
        self.verify_url = reverse('recruitment:verify_document',
                                  args=[self.application.pk, self.version.pk])

    def test_verified_shows_done_and_change_needs_a_reason(self):
        self.client.force_login(self.hr)
        self.assertContains(self.client.get(self.review_url), '>Verify</a>')
        documents.verify(self.hr, self.version, status='verified')
        page = self.client.get(self.review_url)
        self.assertContains(page, 'disabled>&#10003; Verified</button>')
        self.assertNotContains(page, '>Verify</a>')
        verify_page = self.client.get(self.verify_url)
        self.assertContains(verify_page, 'Already recorded: Verified')
        self.assertContains(verify_page, 'Change verification')
        with self.assertRaises(ValidationError):  # same outcome again
            documents.verify(self.hr, self.version, status='verified')
        with self.assertRaises(ValidationError):  # a change without a reason
            documents.verify(self.hr, self.version, status='incomplete', notes='')
        documents.verify(self.hr, self.version, status='incomplete', notes='Page 2 is missing')
        self.version.refresh_from_db()
        self.assertEqual(self.version.verification_status, 'incomplete')
        self.assertEqual(self.version.verifications.count(), 2)  # history kept
        note = Notification.objects.get(user=self.approver, title='Review decision changed')
        self.assertIn('from Verified to Incomplete', note.message)
        self.assertContains(self.client.get(self.review_url), '&#10003; Done</button>')
