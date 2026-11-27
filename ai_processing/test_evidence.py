"""Competency evidence passages from verified documents (advisory, not scored)."""

from django.test import override_settings
from django.urls import reverse

from documents import services as documents
from documents.models import DocumentVersion
from recruitment import application_services as applications
from recruitment.models import FactReviewStatus, FactType
from recruitment.test_applications import ApplicationTestCase

from . import matching, services
from .models import AIExtraction, AIJob, CompetencyEvidence, CompetencyMatch, JobStatus, JobType
from .test_matching import FakeSBERT

PAGE_TEXT = ('Personal Data Sheet of Appl Santos, appl.santos@example.com, 0917 123 4567.\n'
             'Taught calculus and differential equations to first year students for three '
             'years.\nLunch menu and parking notes for the office staff of the building.')


@override_settings(AI_COMPETENCY_MATCHING_ENABLED=True, AI_RETRY_SECONDS=0, AI_MAX_ATTEMPTS=2,
                   AI_EVIDENCE_MIN_SIMILARITY=0.1)
class EvidenceTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()  # competency "Calculus"; applicant "Appl Santos"
        self.user = self.application.applicant.user
        self.user.last_name = 'Santos'
        self.user.save(update_fields=['last_name'])
        self.education = self.application.facts.get(fact_type=FactType.EDUCATION)
        applications.review_fact(self.hr, self.education, decision=FactReviewStatus.ACCEPTED)
        self.version = DocumentVersion.objects.get()
        documents.verify(self.hr, self.version, status='verified')
        self.open_review(self.application)  # matching waits for the qualification review gate
        self.extraction = self.read_by_ai(self.version, PAGE_TEXT)

    def read_by_ai(self, version, text):
        job = AIJob.objects.create(
            application=self.application, document_version=version,
            job_type=JobType.DOCUMENT_EXTRACTION, status=JobStatus.COMPLETED,
            model_name='test', model_version='test', input_hash='x' * 64,
            deduplication_key=f'test-{version.pk}')
        return AIExtraction.objects.create(job=job, document_version=version,
                                           parser_version='test',
                                           pages=[{'page': 2, 'method': 'text', 'text': text}])

    def run_matching(self):
        job = matching.queue_matching(self.hr, self.application)[0]
        with FakeSBERT() as fake:
            services.run_once('test-worker')
        job.refresh_from_db()
        return job, fake

    def test_passages_are_split_and_masked(self):
        passages = matching.document_passages(self.application)
        texts = [p['text'] for p in passages]
        self.assertEqual(len(texts), 3)
        self.assertTrue(all(p['page'] == 2 and p['version'] == self.version for p in passages))
        joined = ' '.join(texts)
        self.assertNotIn('Santos', joined)
        self.assertNotIn('Appl ', joined)
        self.assertNotIn('@', joined)
        self.assertNotIn('4567', joined)
        self.assertIn('[name]', joined)
        self.assertEqual(matching.split_passages('Short page heading.'), ['Short page heading.'])
        self.assertEqual(matching.split_passages('12 34 56 78 90 12 34 56 78 90 12 34'), [])

    def test_matching_stores_the_closest_passages_without_changing_fact_scores(self):
        job, fake = self.run_matching()
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(CompetencyMatch.objects.filter(job=job).count(), 1)  # the one fact
        [best, *_] = CompetencyEvidence.objects.filter(job=job).order_by('rank')
        self.assertIn('calculus', best.excerpt)
        self.assertEqual(best.page_number, 2)
        self.assertEqual(best.document_version, self.version)
        self.assertNotIn('Santos', ' '.join(fake.texts))  # masked before embedding
        self.assertEqual(job.input_manifest['documents'],
                         [{'version': str(self.version.pk), 'extraction': str(self.extraction.pk)}])
        page = self.client
        page.force_login(self.hr)
        response = page.get(reverse('ai_processing:matches', args=[self.application.pk, job.pk]))
        self.assertContains(response, 'not reviewed by HR')
        self.assertContains(response, 'Taught calculus')
        self.assertContains(response, 'page 2')

    def test_only_verified_current_documents_are_read(self):
        documents.verify(self.hr, self.version, status='incomplete', notes='Page missing')
        self.assertEqual(matching.document_passages(self.application), [])
        self.assertEqual(matching.check_inputs(self.application)[3]['documents'], [])

    def test_a_change_in_verification_makes_the_result_out_of_date(self):
        job, _ = self.run_matching()
        self.assertTrue(matching.is_current(job))
        documents.verify(self.hr, self.version, status='invalid', notes='Wrong file')
        self.assertFalse(matching.is_current(job))

    def test_documents_alone_are_enough_to_run(self):
        applications.change_fact_decision(self.hr, self.education,
                                          decision=FactReviewStatus.REJECTED, reason='Mistake')
        self.assertEqual(matching.problems(self.application), [])
        job, _ = self.run_matching()
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertFalse(CompetencyMatch.objects.filter(job=job).exists())
        self.assertTrue(CompetencyEvidence.objects.filter(job=job).exists())
        [(competency, items)] = matching.by_competency(job)
        self.assertEqual(items, [])  # the competency is still listed

    def test_passages_below_the_minimum_are_not_kept(self):
        with override_settings(AI_EVIDENCE_MIN_SIMILARITY=0.99):
            job, _ = self.run_matching()
        self.assertFalse(CompetencyEvidence.objects.filter(job=job).exists())
