"""Stage 10 (13.1-13.3) tests: AI job eligibility, deduplication, worker
retries and recovery, invalidation, and HR review of extracted items.

The OCR/PDF runtime is replaced by a fake engine, so these tests run on any
computer. The real runtime is checked on the server with deploy/ai_check.py
and `manage.py ai_extract_file`.
"""

from datetime import timedelta
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import roles
from accounts.test_stage3 import make_user
from audit.models import AuditLog
from documents import services as documents
from documents.models import FactEvidence, VerificationStatus
from recruitment import application_services as applications
from recruitment.models import ApplicationStatus, FactReviewStatus, FactType
from recruitment.test_applications import ApplicationTestCase, pdf

from . import engine, parsers, services
from .models import (AIJob, AIJobAttempt, AttemptStatus, ExtractionItem, ItemReviewStatus,
                     JobStatus)

TOR_TEXT = """LEYTE SAMPLE STATE UNIVERSITY
College of Education
OFFICIAL TRANSCRIPT OF RECORDS
Name: MARIA SAMPLE SANTOS
Degree: Bachelor of Secondary Education
Major in Mathematics
Date of Graduation: March 25, 2015
MATH 101 College Algebra 1.50 3"""


def fake_pages(text=TOR_TEXT):
    return [{'page': 1, 'method': 'text', 'text': text, 'characters': len(text),
             'ocr_confidence': None}]


class FakeEngine:
    """Patches the engine: returns the given pages, or raises."""

    def __init__(self, pages=None, error=None):
        self.pages = pages if pages is not None else fake_pages()
        self.error = error
        self.calls = 0

    def extract(self, path, mime_type, config=None, heartbeat=lambda: None):
        self.calls += 1
        heartbeat()
        if self.error:
            raise self.error
        return self.pages

    def __enter__(self):
        self.patches = [
            mock.patch.object(engine, 'extract_pages', self.extract),
            mock.patch.object(engine, 'versions', lambda: {'fake': '1'}),
            mock.patch.object(engine, 'load_nlp', lambda: None),
            # The test vacancy only requires a PDS; read it with the TOR rules.
            mock.patch.dict(parsers.PARSERS, {'PDS': (
                lambda pages, _type, nlp: parsers.education_items(pages, 'TOR', nlp),)}),
        ]
        for p in self.patches:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self.patches):
            p.stop()


@override_settings(AI_EXTRACTION_ENABLED=True, AI_RETRY_SECONDS=0, AI_MAX_ATTEMPTS=2)
class AITestCase(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.version = self.application.documents.get().current_version()

    def verify(self, status=VerificationStatus.VERIFIED, notes=''):
        documents.verify(self.hr, self.version, status=status,
                         notes=notes or ('' if status == VerificationStatus.VERIFIED else 'bad'))
        self.version.refresh_from_db()

    def job(self):
        return AIJob.objects.get(document_version=self.version, invalidated_at__isnull=True)

    def run_job(self, fake=None):
        with (fake or FakeEngine()):
            return services.run_once('test-worker')

    def extracted(self):
        self.verify()
        self.run_job()
        return ExtractionItem.objects.get(extraction__job__document_version=self.version)


class QueueTests(AITestCase):
    def test_verification_queues_one_job_and_repeats_do_not_duplicate(self):
        self.assertFalse(AIJob.objects.exists())
        self.verify()
        job = self.job()
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.assertEqual(job.input_manifest['sha256'], self.version.sha256)
        self.assertEqual(job.input_snapshot, self.application.latest_snapshot())
        again, created = services.queue_extraction(self.hr, self.version)
        self.assertFalse(created)
        self.assertEqual(again.pk, job.pk)
        self.verify()  # verified again: still the same job
        self.assertEqual(AIJob.objects.count(), 1)
        self.assertTrue(AuditLog.objects.filter(action='ai.job_queued', job=job).exists())

    def test_only_verified_current_documents_are_eligible(self):
        with self.assertRaises(ValidationError):
            services.queue_extraction(self.hr, self.version)  # pending verification
        self.verify(VerificationStatus.INCOMPLETE)
        self.assertFalse(AIJob.objects.exists())
        with self.assertRaises(ValidationError):
            services.queue_extraction(self.hr, self.version)

    @override_settings(AI_EXTRACTION_ENABLED=False)
    def test_nothing_is_queued_automatically_when_ai_is_off(self):
        self.verify()
        self.assertFalse(AIJob.objects.exists())
        job, created = services.queue_extraction(self.hr, self.version)  # manual request
        self.assertTrue(created)

    def test_only_assigned_reviewers_can_queue(self):
        self.verify()
        with self.assertRaises(PermissionDenied):
            services.queue_extraction(self.hr2, self.version)

    @override_settings(AI_EXTRACTION_TYPES=['TOR'])
    def test_types_not_configured_are_not_processed(self):
        self.verify()
        self.assertFalse(AIJob.objects.exists())

    def test_revoked_verification_invalidates_and_new_verification_requeues(self):
        self.verify()
        first = self.job()
        self.verify(VerificationStatus.INVALID)
        first.refresh_from_db()
        self.assertTrue(first.is_invalidated)
        self.verify()
        second = self.job()
        self.assertNotEqual(first.pk, second.pk)
        self.assertNotEqual(first.deduplication_key, second.deduplication_key)


class WorkerTests(AITestCase):
    def test_successful_run_stores_text_items_and_versions(self):
        item = self.extracted()
        job = self.job()
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.extraction.pages[0]['text'], TOR_TEXT)
        self.assertEqual(job.extraction.engine_versions, {'fake': '1'})
        self.assertEqual(item.item_type, 'education')
        self.assertEqual(item.proposed_value['degree_level'], 'bachelor')
        self.assertIn('Mathematics', item.proposed_value['program'])
        self.assertEqual(item.proposed_value['completed_on'], '2015-03-25')
        self.assertEqual(item.page_number, 1)
        attempt = job.attempts.get()
        self.assertEqual(attempt.status, AttemptStatus.SUCCEEDED)
        self.assertTrue(AuditLog.objects.filter(action='ai.extraction_completed',
                                                job=job).exists())

    def test_no_items_means_needs_review_not_an_empty_success(self):
        self.verify()
        self.run_job(FakeEngine(pages=fake_pages('Unrelated letter text with no degree.')))
        job = self.job()
        self.assertEqual(job.status, JobStatus.NEEDS_REVIEW)
        self.assertTrue(job.extraction.warnings)
        self.assertFalse(job.extraction.items.exists())

    def test_runtime_missing_is_retried_then_failed_and_manual_review_continues(self):
        self.verify()
        failing = FakeEngine(error=engine.EngineUnavailable('not installed'))
        self.run_job(failing)
        job = self.job()
        self.assertEqual(job.status, JobStatus.QUEUED)  # retry scheduled
        self.run_job(failing)
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertEqual(job.attempts.count(), 2)
        self.assertIn('not installed', job.last_error)
        fact = self.application.facts.filter(fact_type=FactType.EDUCATION).first()
        applications.review_fact(self.hr, fact, decision=FactReviewStatus.ACCEPTED)
        services.retry(self.hr, job)
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.QUEUED)
        self.run_job()
        job.refresh_from_db()
        self.assertEqual(job.status, JobStatus.COMPLETED)
        self.assertEqual(job.attempts.count(), 3)

    def test_unreadable_file_fails_without_retry(self):
        self.verify()
        self.run_job(FakeEngine(error=engine.ExtractionError('damaged')))
        self.assertEqual(self.job().status, JobStatus.FAILED)

    def test_stale_run_is_recovered(self):
        self.verify()
        attempt = services.claim_next('dead-worker')
        AIJobAttempt.objects.filter(pk=attempt.pk).update(
            heartbeat_at=timezone.now() - timedelta(hours=1))
        self.assertEqual(services.recover_stale(), 1)
        attempt.refresh_from_db()
        self.assertEqual(attempt.status, AttemptStatus.ABANDONED)
        self.assertEqual(self.job().status, JobStatus.QUEUED)
        self.run_job()
        self.assertEqual(self.job().status, JobStatus.COMPLETED)

    def test_replacement_during_processing_discards_results(self):
        self.verify()
        job = self.job()
        attempt = services.claim_next('w')
        # The applicant replaces the file (via a correction request) mid-run.
        applications.request_correction(self.hr, self.application, message='Replace the PDS',
                                        document_types=[self.pds],
                                        deadline_at=timezone.now() + timedelta(days=2))
        documents.upload(self.applicant, self.application, None, pdf('pds2.pdf', b'%PDF-1.4 v2'),
                         replaces=self.version.document)
        with FakeEngine():
            services.process(attempt)
        job.refresh_from_db()
        self.assertTrue(job.is_invalidated)
        self.assertEqual(job.status, JobStatus.FAILED)
        self.assertFalse(ExtractionItem.objects.exists())

    def test_ai_never_changes_application_status(self):
        before = self.application.status
        self.extracted()
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, before)


class ReviewTests(AITestCase):
    def test_accept_creates_reviewed_fact_with_evidence_and_keeps_proposal(self):
        item = self.extracted()
        self.application.refresh_from_db()
        revision = self.application.revision
        fact = services.accept_item(self.hr, item)
        self.assertEqual(fact.review_status, FactReviewStatus.ACCEPTED)
        self.assertEqual(fact.value['extraction_item_id'], str(item.pk))
        link = FactEvidence.objects.get(application_fact=fact)
        self.assertEqual(link.extraction_item, item)
        self.assertEqual(link.document_version, self.version)
        item.refresh_from_db()
        self.assertEqual(item.review_status, ItemReviewStatus.ACCEPTED)
        self.application.refresh_from_db()
        self.assertEqual(self.application.revision, revision + 1)
        self.assertTrue(AuditLog.objects.filter(action='ai.item_accepted',
                                                job=item.extraction.job).exists())
        with self.assertRaises(ValidationError):
            services.accept_item(self.hr, item)  # already reviewed

    def test_correction_needs_reason_and_keeps_original(self):
        item = self.extracted()
        value = {**item.proposed_value, 'institution': 'Corrected University'}
        with self.assertRaises(ValidationError):
            services.accept_item(self.hr, item, value=value)
        fact = services.accept_item(self.hr, item, value=value, reason='Name on the seal')
        item.refresh_from_db()
        self.assertEqual(item.review_status, ItemReviewStatus.CORRECTED)
        self.assertEqual(item.proposed_value['institution'], 'Leyte Sample State University')
        self.assertEqual(fact.value['institution'], 'Corrected University')

    def test_missing_required_field_must_be_filled_first(self):
        self.verify()
        text = 'Degree: Bachelor of Arts in History'  # no institution, no graduation
        self.run_job(FakeEngine(pages=fake_pages(text)))
        item = ExtractionItem.objects.get()
        with self.assertRaises(ValidationError):
            services.accept_item(self.hr, item)

    def test_reject_needs_reason(self):
        item = self.extracted()
        with self.assertRaises(ValidationError):
            services.reject_item(self.hr, item, reason='')
        services.reject_item(self.hr, item, reason='Wrong document')
        item.refresh_from_db()
        self.assertEqual(item.review_status, ItemReviewStatus.REJECTED)
        self.assertIsNone(item.resulting_fact)

    def test_link_to_existing_fact_avoids_double_counting(self):
        item = self.extracted()
        existing = self.application.facts.get(fact_type=FactType.EDUCATION)
        before = self.application.facts.filter(fact_type=FactType.EDUCATION).count()
        services.link_item(self.hr, item, existing)
        self.assertEqual(self.application.facts.filter(fact_type=FactType.EDUCATION).count(),
                         before)
        self.assertTrue(FactEvidence.objects.filter(application_fact=existing,
                                                    extraction_item=item).exists())

    def test_matching_highlights_same_and_conflicting_facts(self):
        item = self.extracted()
        fact = services.accept_item(self.hr, item)
        other = ExtractionItem(item_type='education', proposed_value={
            **item.proposed_value, 'completed_on': '2016-01-01'})
        [(match, same, differing)] = services.matching_facts(other, [fact])
        self.assertEqual(match, fact)
        self.assertFalse(same)
        self.assertEqual(differing, ['completed_on'])

    def test_invalidated_items_cannot_be_used(self):
        item = self.extracted()
        self.verify(VerificationStatus.REJECTED)
        with self.assertRaises(ValidationError):
            services.accept_item(self.hr, item)

    def test_non_reviewers_cannot_review(self):
        item = self.extracted()
        with self.assertRaises(PermissionDenied):
            services.accept_item(self.hr2, item)

    def test_resubmission_keeps_facts_accepted_from_ai(self):
        item = self.extracted()
        fact = services.accept_item(self.hr, item)
        applications.start_review(self.hr, self.application)
        applications.request_correction(self.hr, self.application, message='Update profile',
                                        deadline_at=timezone.now() + timedelta(days=2))
        applications.resubmit_corrections(self.applicant, self.application)
        fact.refresh_from_db()
        self.assertTrue(fact.is_current)


class ViewTests(AITestCase):
    def test_review_page_shows_ai_status_and_extraction_page_works(self):
        item = self.extracted()
        job = self.job()
        self.client.force_login(self.hr)
        review = self.client.get(reverse('recruitment:application_review',
                                         args=[self.application.pk]))
        self.assertContains(review, reverse('ai_processing:extraction',
                                            args=[self.application.pk, job.pk]))
        page = self.client.get(reverse('ai_processing:extraction',
                                       args=[self.application.pk, job.pk]))
        self.assertContains(page, 'Advisory')
        self.assertContains(page, 'Secondary Education')
        response = self.client.post(reverse('ai_processing:item_action',
                                            args=[self.application.pk, item.pk]),
                                    {'action': 'accept'})
        self.assertEqual(response.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.review_status, ItemReviewStatus.ACCEPTED)

    def test_edit_page_accepts_corrected_values(self):
        item = self.extracted()
        self.client.force_login(self.hr)
        url = reverse('ai_processing:item_edit', args=[self.application.pk, item.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        data = {k: v or '' for k, v in item.proposed_value.items()}
        data.update(institution='Edited University', reason='Seal shows another name')
        self.client.post(url, data)
        item.refresh_from_db()
        self.assertEqual(item.review_status, ItemReviewStatus.CORRECTED)

    def test_applicants_and_unassigned_staff_cannot_open_ai_pages(self):
        self.extracted()
        job = self.job()
        url = reverse('ai_processing:extraction', args=[self.application.pk, job.pk])
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(url).status_code, 403)
        outsider = make_user('outsider_hr', roles.HR)
        self.client.force_login(outsider)
        self.assertIn(self.client.get(url).status_code, (403, 404))

    def test_run_button_queues_and_breadcrumb_points_back(self):
        with override_settings(AI_EXTRACTION_ENABLED=False):
            self.verify()  # verified while AI was off: nothing queued
        self.assertFalse(AIJob.objects.exists())
        self.client.force_login(self.hr)
        review = self.client.get(reverse('recruitment:application_review',
                                         args=[self.application.pk]))
        self.assertContains(review, reverse('ai_processing:run',
                                            args=[self.application.pk, self.version.pk]))
        self.client.post(reverse('ai_processing:run', args=[self.application.pk,
                                                            self.version.pk]))
        job = self.job()
        page = self.client.get(reverse('ai_processing:extraction',
                                       args=[self.application.pk, job.pk]))
        self.assertContains(page, 'Waiting for the AI worker')
        self.assertContains(page, reverse('recruitment:application_review',
                                          args=[self.application.pk]))


class ParserTests(SimpleTestCase):
    def propose(self, code, text):
        return parsers.propose(code, fake_pages(text))[0]

    def test_transcript(self):
        [item] = self.propose('TOR', TOR_TEXT)
        value = item['proposed_value']
        self.assertEqual(value['program'], 'Bachelor of Secondary Education Major in Mathematics')
        self.assertEqual(value['institution'], 'Leyte Sample State University')
        self.assertEqual(value['completion_status'], 'completed')
        self.assertEqual(item['source_location']['lines'], [5, 5])

    def test_diploma(self):
        [item] = self.propose('DIPLOMA', 'SAMPLE INSTITUTE OF TECHNOLOGY\nconfers upon\nX\n'
                                         'Master of Arts in Education\n'
                                         'Given this 30th day of April 2019 at Tacloban City.')
        self.assertEqual(item['proposed_value']['degree_level'], 'master')
        self.assertEqual(item['proposed_value']['completed_on'], '2019-04-30')

    def test_training_certificate(self):
        [item] = self.propose('TRAINING', 'CERTIFICATE OF COMPLETION\n'
                              'for having successfully completed the "Outcomes-Based Teaching '
                              'Workshop"\nheld on March 3-5, 2019 at Ormoc City.\n'
                              'with a total of 24 hours.\nConducted by the Sample Center')
        value = item['proposed_value']
        self.assertEqual(value['title'], 'Outcomes-Based Teaching Workshop')
        self.assertEqual(value['hours'], '24')
        self.assertEqual((value['start_date'], value['end_date']), ('2019-03-03', '2019-03-05'))
        self.assertEqual(value['provider'], 'Sample Center')

    def test_employment_certificate(self):
        [item] = self.propose('EMPLOYMENT', 'SAMPLE COLLEGE OF ORMOC\nThis is to certify that '
                              'Maria has been employed as Part-time Instructor at Sample College '
                              'of Ormoc\nfrom June 1, 2016 to May 31, 2020.')  # wrapped line
        value = item['proposed_value']
        self.assertEqual(value['position'], 'Part-time Instructor')
        self.assertEqual(value['start_date'], '2016-06-01')
        self.assertEqual(value['end_date'], '2020-05-31')

    def test_prc_license_and_eligibility(self):
        items = self.propose('PRC', 'Republic of the Philippines\n'
                             'PROFESSIONAL REGULATION COMMISSION\n'
                             'Profession: Professional Teacher\nRegistration No.: 1234567\n'
                             'Valid Until: September 1, 2027\nPassed the Licensure Examination '
                             'for Professional Teachers with a rating of 85.40%\n'
                             'given on March 29, 2015')
        by_type = {i['item_type']: i['proposed_value'] for i in items}
        self.assertEqual(by_type['license']['license_number'], '1234567')
        self.assertEqual(by_type['license']['issuing_body'], 'Professional Regulation Commission')
        self.assertEqual(by_type['license']['expires_on'], '2027-09-01')
        self.assertEqual(by_type['eligibility']['rating'], '85.40')
        self.assertEqual(by_type['eligibility']['date_obtained'], '2015-03-29')

    def test_month_and_year_only_is_not_turned_into_a_date(self):
        self.assertIsNone(parsers.parse_date('March 2015'))
        self.assertEqual(parsers.parse_date('25 March 2015'), '2015-03-25')

    def test_unknown_type_and_empty_text_give_warnings(self):
        self.assertEqual(parsers.propose('LETTER', fake_pages('Dear Sir'))[0], [])
        self.assertTrue(parsers.propose('TOR', fake_pages(''))[1])


class ResultsPageTests(AITestCase):
    def test_workspace_lists_jobs_waiting_for_review(self):
        self.client.force_login(self.hr)
        workspace = self.client.get(reverse('core:workspace_hr'))
        self.assertContains(workspace, reverse('ai_processing:results'))
        url = reverse('ai_processing:results')
        self.assertContains(self.client.get(url), 'Nothing is waiting for review.')

        self.verify()  # queued, not yet read
        self.assertContains(self.client.get(url + '?show=running'), 'pds.pdf')
        self.run_job()
        page = self.client.get(url)
        self.assertContains(page, 'pds.pdf')
        self.assertContains(page, '1 of 1 waiting')
        item = ExtractionItem.objects.get()
        services.reject_item(self.hr, item, reason='Not on the document.')
        self.assertNotContains(self.client.get(url), 'pds.pdf')
        self.assertContains(self.client.get(url + '?show=done'), 'pds.pdf')

    def test_only_hr_with_access_sees_results(self):
        self.verify()
        self.run_job()
        outsider = make_user('hr_other', roles.HR)  # not assigned to the vacancy
        self.client.force_login(outsider)
        self.assertNotContains(self.client.get(reverse('ai_processing:results')), 'pds.pdf')
        self.client.force_login(self.applicant)
        self.assertNotEqual(self.client.get(reverse('ai_processing:results')).status_code, 200)
