"""AI acceptance scenario (Development Guide 13.6).

One application goes through every AI feature with AI switched on, using the
fake reading engine and fake SBERT model (no AI packages needed):
verified-only processing, HR correction of a proposal, conflicting evidence
from a second document, qualification checks, competency matching, a
document replacement during processing, retries, permission boundaries, and
the official roster: an accepted AI correction follows the same new-version
and approval path as a manual one, and approved exports never change.
"""

from datetime import timedelta

from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import roles
from accounts.test_stage3 import make_user
from documents import services as documents
from documents.models import Document, FactEvidence
from matrix import services as rosters
from matrix.models import RosterStatus
from matrix.tests import RosterTestCase
from recruitment import application_services as applications
from recruitment.models import ApplicationStatus, FactReviewStatus, FactType
from recruitment.test_applications import PDF, pdf

from . import engine, matching, screening, services
from .models import AIJob, ExtractionItem, ItemReviewStatus, JobStatus, JobType
from .test_matching import FakeSBERT
from .tests import FakeEngine


@override_settings(AI_EXTRACTION_ENABLED=True, AI_QUALIFICATION_CHECKS_ENABLED=True,
                   AI_COMPETENCY_MATCHING_ENABLED=True, AI_RETRY_SECONDS=0, AI_MAX_ATTEMPTS=2)
class AIAcceptanceScenario(RosterTestCase):
    def worker(self, fake=None):
        with (fake or FakeEngine()), FakeSBERT():
            return services.run_once('acceptance-worker')

    def roster_values(self, roster):
        return list(roster.entries.order_by('sequence_number').values_list('exported_values',
                                                                         flat=True))

    def test_ai_assists_without_taking_over(self):
        user = self.applicant_with('Reyes')
        application = self.submit_as(user)
        self.accept_all(application)  # HR's manual review; verifies the PDS too
        application.refresh_from_db()

        # --- The official roster is approved and released before any AI result.
        roster = self.generate()
        self.acknowledge_all(roster)
        self.assertEqual(rosters.submit_for_approval(self.hr, roster), [])
        rosters.approve(self.approver, roster)
        roster.refresh_from_db()
        rosters.release(self.approver, roster)
        roster.refresh_from_db()
        approved_values = self.roster_values(roster)
        official = list(roster.exports.filter(is_draft=False))
        approved_bytes = {e.pk: rosters.export_path(e).read_bytes() for e in official}

        # --- Verified-only: verification queued the PDS; nothing else was queued.
        pds = application.documents.get().current_version()
        job = AIJob.objects.get(job_type=JobType.DOCUMENT_EXTRACTION)
        self.assertEqual(job.document_version, pds)

        # --- Retries: the runtime is missing once, then the run succeeds.
        self.worker(FakeEngine(error=engine.EngineUnavailable('pytesseract is missing')))
        job.refresh_from_db()
        self.assertEqual((job.status, job.attempt_count), (JobStatus.QUEUED, 1))
        self.worker()
        job.refresh_from_db()
        self.assertIn(job.status, (JobStatus.COMPLETED, JobStatus.NEEDS_REVIEW))
        item = ExtractionItem.objects.get(extraction__job=job)

        # Extraction alone changed nothing official.
        application.refresh_from_db()
        roster.refresh_from_db()
        revision = application.revision
        self.assertFalse(roster.requires_regeneration)
        self.assertEqual(application.status, ApplicationStatus.SUBMITTED)

        # --- Permission boundaries: the approver and other applicants cannot act on items.
        self.client.force_login(self.approver)
        self.client.post(reverse('ai_processing:item_action', args=[application.pk, item.pk]),
                         {'action': 'reject', 'reason': 'x'})
        item.refresh_from_db()
        self.assertEqual(item.review_status, ItemReviewStatus.PENDING)
        stranger = make_user('stranger', roles.APPLICANT)
        self.client.force_login(stranger)
        page = self.client.get(reverse('ai_processing:extraction', args=[application.pk, job.pk]))
        self.assertIn(page.status_code, (302, 403, 404))
        with self.assertRaises(PermissionDenied):
            services.accept_item(stranger, item)

        # --- HR corrects the proposal (manual correction of AI output).
        corrected = dict(item.proposed_value, institution='Leyte Sample State University (Main)')
        fact = services.accept_item(self.hr, item, value=corrected,
                                    reason='Campus name as printed on the diploma.')
        item.refresh_from_db()
        self.assertEqual(item.review_status, ItemReviewStatus.CORRECTED)
        self.assertNotEqual(item.proposed_value, corrected)  # the proposal is kept
        self.assertTrue(FactEvidence.objects.filter(application_fact=fact,
                                                    extraction_item=item).exists())

        # The correction affects roster content: same path as a manual correction.
        application.refresh_from_db()
        roster.refresh_from_db()
        self.assertGreater(application.revision, revision)
        self.assertTrue(roster.requires_regeneration)
        self.assertEqual(self.roster_values(roster), approved_values)
        for export in official:
            self.assertEqual(rosters.export_path(export).read_bytes(), approved_bytes[export.pk])
        new_roster = self.generate(cutoff_at=roster.cutoff_at)
        self.assertEqual(new_roster.version, 2)
        self.acknowledge_all(new_roster)
        self.assertEqual(rosters.submit_for_approval(self.hr, new_roster), [])
        self.assertEqual(rosters.approve(self.approver, new_roster), [])
        roster.refresh_from_db()
        self.assertEqual(roster.status, RosterStatus.SUPERSEDED)

        # --- Advisory checks and matching never change the application or roster.
        new_roster.refresh_from_db()
        revision = application.revision
        check, _ = screening.run_checks(self.hr, application)
        self.assertTrue(check.results.exists())
        match_job, _ = matching.queue_matching(self.hr, application)
        self.worker()
        match_job.refresh_from_db()
        self.assertEqual(match_job.status, JobStatus.COMPLETED)
        application.refresh_from_db()
        new_roster.refresh_from_db()
        self.assertEqual(application.revision, revision)
        self.assertFalse(new_roster.requires_regeneration)
        self.assertFalse(application.facts.filter(
            fact_type=FactType.QUALIFICATION_ASSESSMENT).exists())

        # --- Conflicting evidence: the same qualification read again is linked, not duplicated.
        second = ExtractionItem.objects.create(
            extraction=item.extraction, item_type=item.item_type, raw_text=item.raw_text,
            proposed_value=item.proposed_value, page_number=item.page_number)
        education_count = application.facts.filter(
            fact_type=FactType.EDUCATION, review_status=FactReviewStatus.ACCEPTED).count()
        self.assertIn(fact, [m[0] for m in services.matching_facts(
            second, list(application.facts.exclude(review_status=FactReviewStatus.SUPERSEDED)))])
        services.link_item(self.hr, second, fact, reason='Same degree as the diploma item.')
        self.assertEqual(application.facts.filter(
            fact_type=FactType.EDUCATION, review_status=FactReviewStatus.ACCEPTED).count(),
            education_count)

        # --- Replacement while a job is queued: the old job is invalidated, the
        # new version waits for HR verification, and late results are discarded.
        applications.request_correction(self.hr, application, message='Upload a clearer PDS',
                                        document_types=[self.pds],
                                        deadline_at=timezone.now() + timedelta(days=3))
        extraction_jobs = AIJob.objects.filter(job_type=JobType.DOCUMENT_EXTRACTION)
        self.assertIsNone(services.claim_next('acceptance-worker'))  # nothing queued
        replacement = documents.upload(user, application, self.pds,
                                       pdf('pds-clear.pdf', PDF + b'clear'),
                                       replaces=Document.objects.get(application=application))
        job.refresh_from_db()
        self.assertTrue(job.is_invalidated)
        self.assertFalse(extraction_jobs.filter(document_version=replacement,
                                          invalidated_at__isnull=True).exists())
        # Facts HR accepted from the AI are kept through the resubmission.
        applications.resubmit_corrections(user, application)
        fact.refresh_from_db()
        self.assertEqual(fact.review_status, FactReviewStatus.ACCEPTED)

        # --- The released version 1 exports are still byte-for-byte the same.
        for export in official:
            self.assertEqual(rosters.export_path(export).read_bytes(), approved_bytes[export.pk])
