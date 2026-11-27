"""Step 2 of the qualification-review plan: the review opens only when the
required documents are decided, the facts are decided, and (with AI on)
extraction is finished or skipped by HR with a reason."""

from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse

from ai_processing import services as ai
from ai_processing.models import AIJob
from audit.models import AuditLog
from documents import services as documents
from documents.models import DocumentVersion, VerificationStatus

from . import application_services as services
from . import review_gate
from .models import FactReviewStatus
from .test_applications import ApplicationTestCase


@override_settings(AI_EXTRACTION_ENABLED=False)
class ReviewGateTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.version = DocumentVersion.objects.get()
        self.standard = self.application.latest_snapshot().requirement_version \
            .qualification_standards.get()
        self.review_url = reverse('recruitment:application_review', args=[self.application.pk])
        self.outcome_url = reverse('recruitment:qualification_outcome',
                                   args=[self.application.pk, self.standard.pk])

    def decide_facts(self):
        for fact in self.application.facts.filter(review_status=FactReviewStatus.PENDING):
            services.review_fact(self.hr, fact, decision=FactReviewStatus.ACCEPTED)

    def test_closed_until_documents_and_facts_are_decided(self):
        self.assertFalse(review_gate.status(self.application)['ready'])
        documents.verify(self.hr, self.version, status=VerificationStatus.VERIFIED)
        self.assertFalse(review_gate.status(self.application)['ready'])  # facts pending
        self.decide_facts()
        self.assertTrue(review_gate.status(self.application)['ready'])
        self.assertEqual(review_gate.problems(self.application), [])

    def test_any_decision_counts_and_rejected_facts_count(self):
        documents.verify(self.hr, self.version, status=VerificationStatus.REJECTED,
                         notes='Blank page')
        for fact in self.application.facts.filter(review_status=FactReviewStatus.PENDING):
            services.review_fact(self.hr, fact, decision=FactReviewStatus.REJECTED,
                                 reason='Not proven')
        self.assertTrue(review_gate.status(self.application)['ready'])

    def test_recording_an_outcome_is_refused_while_closed(self):
        with self.assertRaises(ValidationError):
            services.record_qualification_outcome(self.hr, self.application, self.standard,
                                                  outcome='meets')
        self.client.force_login(self.hr)
        response = self.client.get(self.outcome_url)
        self.assertRedirects(response, self.review_url + '#qualifications',
                             fetch_redirect_response=False)

    def test_review_page_shows_what_is_missing(self):
        self.client.force_login(self.hr)
        page = self.client.get(self.review_url)
        self.assertContains(page, 'The qualification review opens when:')
        self.assertContains(page, 'Required documents decided: 0 of 1 (waiting: '
                                  'Personal Data Sheet (CSC Form 212) v1)')
        self.assertContains(page, 'Facts decided (accepted or rejected): 0 of')
        self.assertNotContains(page, '>Record outcome</a>')
        self.assertNotContains(page, 'AI extraction')  # AI off: not a condition
        documents.verify(self.hr, self.version, status=VerificationStatus.VERIFIED)
        self.decide_facts()
        page = self.client.get(self.review_url)
        self.assertNotContains(page, 'The qualification review opens when:')
        self.assertContains(page, '>Record outcome</a>')

    def test_advisory_check_follows_the_gate(self):
        from ai_processing import screening
        with override_settings(AI_QUALIFICATION_CHECKS_ENABLED=True):
            self.assertTrue(any('qualification review opens' in p
                                for p in screening.problems(self.application)))
            documents.verify(self.hr, self.version, status=VerificationStatus.VERIFIED)
            self.decide_facts()
            self.assertEqual(screening.problems(self.application), [])


@override_settings(AI_EXTRACTION_ENABLED=True, AI_EXTRACTION_TYPES=['PDS'])
class ReviewGateExtractionTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.version = DocumentVersion.objects.get()
        self.review_url = reverse('recruitment:application_review', args=[self.application.pk])
        documents.verify(self.hr, self.version, status=VerificationStatus.VERIFIED)  # queues
        for fact in self.application.facts.filter(review_status=FactReviewStatus.PENDING):
            services.review_fact(self.hr, fact, decision=FactReviewStatus.ACCEPTED)

    def test_waits_for_extraction_then_continue_without_ai(self):
        self.assertTrue(AIJob.objects.filter(status='queued').exists())
        found = review_gate.status(self.application)
        self.assertFalse(found['ready'])
        self.assertIn((False, 'AI extraction still running: Personal Data Sheet (CSC Form 212) '
                              'v1'), found['items'])
        self.client.force_login(self.hr)
        page = self.client.get(self.review_url)
        self.assertContains(page, 'Continue without AI')
        with self.assertRaises(ValidationError):
            ai.skip_running_extraction(self.hr, self.application, reason=' ')
        skip = reverse('ai_processing:skip_extraction', args=[self.application.pk])
        self.client.post(skip, {'reason': 'Worker is down'})
        self.assertTrue(review_gate.status(self.application)['ready'])
        job = AIJob.objects.get()
        self.assertIn('HR continued without AI: Worker is down', job.invalidation_reason)
        self.assertTrue(AuditLog.objects.filter(action='ai.extraction_skipped',
                                                reason='Worker is down').exists())

    def test_finished_extraction_opens_it(self):
        AIJob.objects.update(status='completed')
        self.assertTrue(review_gate.status(self.application)['ready'])
        self.assertIn((True, 'AI extraction finished'),
                      review_gate.status(self.application)['items'])

    def test_failed_extraction_does_not_block(self):
        AIJob.objects.update(status='failed')
        self.assertTrue(review_gate.status(self.application)['ready'])
