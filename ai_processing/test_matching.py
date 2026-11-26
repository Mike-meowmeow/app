"""SBERT competency matching (13.5), with a fake embedding model."""

import hashlib
import math
import re
import tempfile
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse

from recruitment import application_services as applications
from recruitment.models import FactReviewStatus, FactType, RelevanceStatus
from recruitment.test_applications import ApplicationTestCase

from . import engine, matching, services
from .models import AIJob, CompetencyMatch, JobStatus

DIMENSIONS = 64


def fake_vector(text):
    """Bag of words hashed into a small unit vector: texts sharing words are
    similar, others are not. Stands in for SBERT in tests."""
    vector = [0.0] * DIMENSIONS
    for word in re.findall(r'[a-z]+', text.lower()):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % DIMENSIONS] += 1.0
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class FakeSBERT:
    def __init__(self, error=None):
        self.error = error
        self.texts = []

    def embed(self, texts):
        if self.error:
            raise self.error
        self.texts.extend(texts)
        return [fake_vector(t) for t in texts]

    def __enter__(self):
        self.patches = [
            mock.patch.object(engine, 'embed', self.embed),
            mock.patch.object(engine, 'sbert_identity', lambda: {
                'model': 'fake-sbert', 'revision': 'abc123', 'dimensions': DIMENSIONS,
                'max_tokens': 256, 'sentence-transformers': 'test', 'torch': 'test'}),
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


@override_settings(AI_COMPETENCY_MATCHING_ENABLED=True, AI_RETRY_SECONDS=0, AI_MAX_ATTEMPTS=2)
class MatchingTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()  # competency "Calculus" on the vacancy
        self.education = self.application.facts.get(fact_type=FactType.EDUCATION)
        applications.review_fact(self.hr, self.education, decision=FactReviewStatus.ACCEPTED)
        self.training = applications.add_manual_fact(
            self.hr, self.application, FactType.TRAINING,
            {'title': 'Teaching Calculus Workshop', 'provider': 'Sample Office', 'hours': '8',
             'start_date': None, 'end_date': None}, reason='From the training certificate.')

    def run_worker(self, fake=None):
        with (fake or FakeSBERT()):
            return services.run_once('test-worker')

    def queued(self):
        return matching.queue_matching(self.hr, self.application)[0]

    def test_queue_run_and_store_every_pair(self):
        self.application.refresh_from_db()
        revision, status = self.application.revision, self.application.status
        job, created = matching.queue_matching(self.hr, self.application)
        self.assertTrue(created)
        self.assertEqual(job.status, JobStatus.QUEUED)
        again, created_again = matching.queue_matching(self.hr, self.application)
        self.assertEqual((again, created_again), (job, False))  # no duplicates
        self.run_worker()
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertIn('fake-sbert @ abc123', job.model_version)
        self.assertEqual(job.configuration['runtime']['revision'], 'abc123')
        matches = list(CompetencyMatch.objects.filter(job=job).order_by('-similarity'))
        self.assertEqual(len(matches), 2)  # one competency x two facts
        self.assertEqual(matches[0].application_fact, self.training)  # shares "calculus"
        self.assertTrue(all(-1 <= m.similarity <= 1 for m in matches))
        self.assertIn('Cosine similarity', matches[0].explanation)
        self.application.refresh_from_db()
        self.assertEqual((self.application.revision, self.application.status), (revision, status))

    def test_inputs_leave_out_identity_and_non_qualification_names(self):
        fake = FakeSBERT()
        self.queued()
        self.run_worker(fake)
        sent = ' '.join(fake.texts)
        self.assertNotIn('Santos', sent)  # applicant's name
        self.assertNotIn('EVSU', sent)  # institution name
        self.assertNotIn('Sample Office', sent)  # training provider
        self.assertIn("Master's degree in MA Mathematics", sent)

    def test_not_relevant_and_pending_facts_are_left_out(self):
        applications.set_relevance(self.hr, self.training, RelevanceStatus.NOT_RELEVANT)
        _, _, facts, _ = matching.check_inputs(self.application)
        self.assertEqual(facts, [self.education])
        applications.change_fact_decision(self.hr, self.education,
                                          decision=FactReviewStatus.REJECTED,
                                          reason='Not supported by the TOR.')
        self.assertTrue(matching.problems(self.application))  # nothing left to compare
        with self.assertRaises(ValidationError):
            matching.queue_matching(self.hr, self.application)

    def test_change_before_storing_discards_results(self):
        job = self.queued()
        applications.set_relevance(self.hr, self.training, RelevanceStatus.NOT_RELEVANT)
        self.run_worker()
        job.refresh_from_db()
        self.assertTrue(job.is_invalidated)
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertFalse(CompetencyMatch.objects.exists())

    def test_change_after_completion_marks_out_of_date_and_rerun_replaces(self):
        first = self.queued()
        self.run_worker()
        first.refresh_from_db()
        self.assertTrue(matching.is_current(first))
        applications.set_relevance(self.hr, self.training, RelevanceStatus.RELEVANT)
        self.assertTrue(matching.is_current(first))  # same texts compared: still current
        applications.set_relevance(self.hr, self.training, RelevanceStatus.NOT_RELEVANT,
                                   reason='Not related to the subject')
        self.assertFalse(matching.is_current(first))
        second, created = matching.queue_matching(self.hr, self.application)
        self.assertTrue(created)
        first.refresh_from_db()
        self.assertTrue(first.is_invalidated)
        self.assertEqual(matching.latest(self.application), second)

    def test_missing_runtime_is_retried_then_failed_and_hr_can_retry(self):
        job = self.queued()
        missing = FakeSBERT(error=engine.EngineUnavailable('sentence_transformers is missing'))
        self.run_worker(missing)
        self.run_worker(missing)
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertIn('missing', job.last_error)
        services.retry(self.hr, job)
        self.run_worker()
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.COMPLETED)

    def test_permissions_and_switch(self):
        with self.assertRaises(PermissionDenied):
            matching.queue_matching(self.applicant, self.application)
        self.client.force_login(self.approver)
        self.client.post(reverse('ai_processing:run_matching', args=[self.application.pk]))
        self.assertFalse(AIJob.objects.filter(job_type='competency_match').exists())
        with override_settings(AI_COMPETENCY_MATCHING_ENABLED=False):
            with self.assertRaises(ValidationError):
                matching.queue_matching(self.hr, self.application)
            self.client.force_login(self.hr)
            page = self.client.get(reverse('recruitment:application_review',
                                           args=[self.application.pk]))
            self.assertNotContains(page, 'Run competency matching')

    def test_pages(self):
        self.client.force_login(self.hr)
        review = reverse('recruitment:application_review', args=[self.application.pk])
        self.assertContains(self.client.get(review), 'Run competency matching')
        response = self.client.post(reverse('ai_processing:run_matching',
                                            args=[self.application.pk]))
        job = AIJob.objects.get(job_type='competency_match')
        self.assertRedirects(response, reverse('ai_processing:matches',
                                               args=[self.application.pk, job.pk]))
        self.assertContains(self.client.get(response.url), 'Waiting for the AI worker')
        self.run_worker()
        page = self.client.get(response.url)
        self.assertContains(page, 'not</strong> a hiring score')
        self.assertContains(page, 'Teaching Calculus Workshop')
        self.assertContains(self.client.get(review), 'All results and details')


@override_settings(AI_COMPETENCY_MATCHING_ENABLED=True)
class EvaluationCommandTests(ApplicationTestCase):
    def test_reports_agreement_with_hr_judgments(self):
        example = Path(__file__).resolve().parent.parent / 'deploy' / 'competency-pairs-example.csv'
        out = Path(tempfile.mkdtemp()) / 'results.csv'
        stdout = StringIO()
        with FakeSBERT():
            call_command('evaluate_competency_matching', str(example), '--out', str(out),
                         stdout=stdout)
        text = stdout.getvalue()
        self.assertIn('Pairs: 10', text)
        self.assertIn('scored above a not-related pair', text)
        self.assertTrue(out.exists())

    def test_rejects_bad_files(self):
        bad = Path(tempfile.mkdtemp()) / 'bad.csv'
        bad.write_text('competency,qualification,hr_judgment\nA,B,maybe\n', encoding='utf-8')
        from django.core.management.base import CommandError
        with FakeSBERT(), self.assertRaises(CommandError):
            call_command('evaluate_competency_matching', str(bad), stdout=StringIO())
