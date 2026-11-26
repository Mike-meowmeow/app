"""Stage 5 tests: submission (FR-006), documents (FR-007), verification
(FR-008), workflow (FR-013), corrections and withdrawal (FR-025), manual fact
and qualification review (FR-009 manual, FR-024 manual)."""

import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import roles
from accounts.test_stage3 import make_user
from applicants.models import Applicant, EducationalRecord
from audit.models import AuditLog
from documents import services as documents
from documents.models import Document, DocumentType, DocumentVersion
from documents.storage import FileRejected

from . import application_services as services
from .models import (Application, ApplicationCorrection, ApplicationStatus, AssignmentRole,
                     CorrectionStatus, FactReviewStatus, FactType)
from .services import assign_staff
from .tests import VacancyTestCase

PDF = b'%PDF-1.4\n% test document\n'
PNG = b'\x89PNG\r\n\x1a\n' + b'\x00' * 32


def pdf(name='file.pdf', content=PDF):
    return SimpleUploadedFile(name, content, content_type='application/pdf')


class ApplicationTestCase(VacancyTestCase):
    def setUp(self):
        super().setUp()
        self.storage_dir = tempfile.mkdtemp()
        self.override = override_settings(DOCUMENT_STORAGE_ROOT=self.storage_dir)
        self.override.enable()
        self.vacancy = self.published()  # PDS required, education standard
        self.other_applicant = make_user('other', roles.APPLICANT)
        self.complete_profile(self.applicant)

    def tearDown(self):
        self.override.disable()
        shutil.rmtree(self.storage_dir, ignore_errors=True)
        super().tearDown()

    def complete_profile(self, user):
        profile, _ = Applicant.objects.get_or_create(user=user)
        user.last_name = user.last_name or 'Santos'
        user.save()
        profile.contact_no = '09171234567'
        profile.birthdate = date(1990, 1, 1)
        profile.sex = 'female'
        profile.address = 'Ormoc City'
        profile.civil_status = 'single'
        profile.save()
        EducationalRecord.objects.create(applicant=profile, degree_level='master',
                                         program='MA Mathematics', institution='EVSU',
                                         completion_status='completed',
                                         completed_on=date(2015, 3, 1))
        return profile

    def stored_files(self):
        return [p for p in Path(self.storage_dir).rglob('*') if p.is_file()]

    def draft(self, user=None):
        return services.start_application(user or self.applicant, self.vacancy)[0]

    def submitted(self, user=None):
        user = user or self.applicant
        application = self.draft(user)
        documents.upload(user, application, self.pds, pdf('pds.pdf'))
        services.submit(user, application)
        application.refresh_from_db()
        return application


class SubmissionTests(ApplicationTestCase):
    def test_one_application_per_vacancy(self):
        first, created = services.start_application(self.applicant, self.vacancy)
        again, created_again = services.start_application(self.applicant, self.vacancy)
        self.assertTrue(created)
        self.assertFalse(created_again)
        self.assertEqual(first.pk, again.pk)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Application.objects.create(applicant=first.applicant, vacancy=self.vacancy)

    def test_cannot_start_when_not_accepting(self):
        upcoming = self.published(opens_at=timezone.now() + timedelta(days=1))
        with self.assertRaises(ValidationError):
            services.start_application(self.applicant, upcoming)
        with self.assertRaises(PermissionDenied):
            services.start_application(self.hr, self.vacancy)

    def test_submission_lists_missing_profile_and_documents(self):
        newbie = make_user('newbie', roles.APPLICANT)  # empty profile
        application = self.draft(newbie)
        problems = services.submission_problems(application)
        self.assertTrue(any('Contact number' in p for p in problems))
        self.assertTrue(any('Personal Data Sheet' in p for p in problems))
        with self.assertRaises(ValidationError):
            services.submit(newbie, application)
        application.refresh_from_db()
        self.assertEqual(application.status, ApplicationStatus.DRAFT)
        self.assertFalse(application.snapshots.exists())

    def test_submit_creates_snapshot_facts_log_and_revision(self):
        application = self.submitted()
        self.assertEqual(application.status, ApplicationStatus.SUBMITTED)
        self.assertIsNotNone(application.submitted_at)
        self.assertEqual(application.revision, 1)
        snapshot = application.snapshots.get()
        self.assertEqual(snapshot.snapshot_type, 'submission')
        self.assertEqual(snapshot.requirement_version, self.vacancy.current_requirements())
        self.assertEqual(snapshot.data['identity']['contact_no'], '09171234567')
        self.assertEqual(snapshot.data['education'][0]['program'], 'MA Mathematics')
        self.assertEqual(snapshot.data['documents'][0]['document_type'], 'PDS')
        facts = application.facts.all()
        self.assertEqual({f.fact_type for f in facts}, {'identity', 'education'})
        self.assertTrue(all(f.review_status == FactReviewStatus.PENDING for f in facts))
        self.assertTrue(application.workflow_logs.filter(to_status='submitted').exists())
        self.assertTrue(AuditLog.objects.filter(action='application.submitted').exists())

    def test_repeated_submit_does_not_duplicate(self):
        application = self.submitted()
        first_time = application.submitted_at
        again, already = services.submit(self.applicant, application)
        self.assertTrue(already)
        self.assertEqual(again.submitted_at, first_time)
        self.assertEqual(application.snapshots.count(), 1)
        self.assertEqual(Application.objects.filter(vacancy=self.vacancy).count(), 1)

    def test_later_profile_changes_do_not_alter_submission(self):
        application = self.submitted()
        profile = Applicant.objects.get(user=self.applicant)
        profile.contact_no = '09999999999'
        profile.save()
        snapshot = application.snapshots.get()
        snapshot.refresh_from_db()
        self.assertEqual(snapshot.data['identity']['contact_no'], '09171234567')
        with self.assertRaises(PermissionError):
            snapshot.save()

    def test_deadline_blocks_submission(self):
        application = self.draft()
        documents.upload(self.applicant, application, self.pds, pdf())
        type(self.vacancy).objects.filter(pk=self.vacancy.pk).update(
            opens_at=timezone.now() - timedelta(days=9),
            deadline_at=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(ValidationError):
            services.submit(self.applicant, application)

    def test_documents_locked_after_submission(self):
        application = self.submitted()
        with self.assertRaises(ValidationError):
            documents.upload(self.applicant, application, self.pds, pdf('again.pdf'))


class DocumentTests(ApplicationTestCase):
    def test_rejects_bad_files_without_storing_anything(self):
        application = self.draft()
        cases = [pdf('fake.pdf', b'just text, not a pdf'),
                 SimpleUploadedFile('image.pdf', PNG, content_type='application/pdf'),
                 pdf('empty.pdf', b'')]
        for upload in cases:
            with self.assertRaises(FileRejected):
                documents.upload(self.applicant, application, self.pds, upload)
        DocumentType.objects.filter(pk=self.pds.pk).update(max_size_bytes=10)
        self.pds.refresh_from_db()
        with self.assertRaises(FileRejected):
            documents.upload(self.applicant, application, self.pds, pdf())
        self.assertFalse(Document.objects.exists())
        self.assertEqual(self.stored_files(), [])

    def test_failed_database_write_leaves_no_file(self):
        application = self.draft()
        with mock.patch.object(DocumentVersion.objects, 'create', side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                documents.upload(self.applicant, application, self.pds, pdf())
        self.assertFalse(Document.objects.exists())
        self.assertEqual(self.stored_files(), [])

    def test_replacement_keeps_earlier_version(self):
        application = self.draft()
        v1 = documents.upload(self.applicant, application, self.pds, pdf('one.pdf'))
        v2 = documents.upload(self.applicant, application, self.pds,
                              pdf('two.pdf', PDF + b'v2'), replaces=v1.document)
        self.assertEqual((v1.version, v2.version), (1, 2))
        self.assertEqual(v1.document.versions.count(), 2)
        self.assertEqual(len(self.stored_files()), 2)
        self.assertEqual(v2.verification_status, 'pending')
        with self.assertRaises(PermissionError):
            v1.file_name = 'renamed.pdf'
            v1.save()

    def test_only_checklist_types_and_max_files(self):
        application = self.draft()
        with self.assertRaises(ValidationError):
            documents.upload(self.applicant, application, self.tor, pdf())
        documents.upload(self.applicant, application, self.pds, pdf())
        with self.assertRaises(ValidationError):
            documents.upload(self.applicant, application, self.pds, pdf('second.pdf'))

    def test_draft_documents_can_be_removed(self):
        application = self.draft()
        version = documents.upload(self.applicant, application, self.pds, pdf())
        with self.captureOnCommitCallbacks(execute=True):
            documents.remove_draft_document(self.applicant, version.document)
        self.assertFalse(Document.objects.exists())
        self.assertEqual(self.stored_files(), [])

    def test_download_permissions(self):
        application = self.submitted()
        version = DocumentVersion.objects.get()
        url = reverse('documents:download', args=[version.pk])
        self.assertEqual(self.client.get(url).status_code, 302)  # anonymous -> login
        for user, expected in ((self.applicant, 200), (self.other_applicant, 404),
                               (self.hr2, 404), (self.hr, 200), (self.approver, 200)):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, expected, user.username)
            if expected == 200:
                self.assertEqual(b''.join(response.streaming_content), PDF)
                self.assertEqual(response['X-Content-Type-Options'], 'nosniff')
        self.assertTrue(AuditLog.objects.filter(action='document.downloaded').exists())
        self.assertTrue(str(Path(self.storage_dir)) in str(
            Path(self.storage_dir) / version.storage_key))
        self.assertNotIn('static', version.storage_key)

    def test_drafts_are_invisible_to_staff(self):
        application = self.draft()
        version = documents.upload(self.applicant, application, self.pds, pdf())
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(reverse('documents:download',
                                                 args=[version.pk])).status_code, 404)
        self.assertEqual(self.client.get(reverse('recruitment:application_review',
                                                 args=[application.pk])).status_code, 404)

    def test_verification_rules_and_history(self):
        application = self.submitted()
        version = DocumentVersion.objects.get()
        with self.assertRaises(ValidationError):
            documents.verify(self.hr, version, status='incomplete', notes='')
        with self.assertRaises(PermissionDenied):
            documents.verify(self.hr2, version, status='verified')
        documents.verify(self.hr, version, status='incomplete', notes='Page 2 missing')
        documents.verify(self.hr, version, status='verified', notes='Missing page received')
        version.refresh_from_db()
        self.assertEqual(version.verification_status, 'verified')
        self.assertEqual(list(version.verifications.values_list('status', flat=True)),
                         ['incomplete', 'verified'])
        application.refresh_from_db()
        self.assertEqual(application.revision, 3)

    def test_verification_queue_lists_pending_latest_versions(self):
        self.submitted()
        self.client.force_login(self.hr)
        response = self.client.get(reverse('recruitment:verification_queue'))
        self.assertContains(response, 'pds.pdf')
        self.client.force_login(self.hr2)
        self.assertNotContains(self.client.get(reverse('recruitment:verification_queue')),
                               'pds.pdf')


class CorrectionAndWithdrawalTests(ApplicationTestCase):
    def deadline(self, days=3):
        return timezone.now() + timedelta(days=days)

    def test_correction_cycle(self):
        application = self.submitted()
        services.start_review(self.hr, application)
        request = services.request_correction(self.hr, application, message='Upload a clear PDS',
                                              document_types=[self.pds],
                                              deadline_at=self.deadline())
        application.refresh_from_db()
        self.assertEqual(application.status, ApplicationStatus.CORRECTION_REQUESTED)
        with self.assertRaises(ValidationError):  # replacement still missing
            services.resubmit_corrections(self.applicant, application)

        original = Document.objects.get()
        replacement = documents.upload(self.applicant, application, self.pds,
                                       pdf('pds-clear.pdf', PDF + b'clear'), replaces=original)
        self.assertEqual(replacement.correction_request, request)
        record = EducationalRecord.objects.get()
        record.program = 'MA Mathematics Education'
        record.save()
        services.resubmit_corrections(self.applicant, application)
        application.refresh_from_db()
        request.refresh_from_db()
        self.assertEqual(application.status, ApplicationStatus.UNDER_REVIEW)
        self.assertEqual(request.status, CorrectionStatus.SUBMITTED)
        snapshot = application.latest_snapshot()
        self.assertEqual((snapshot.version, snapshot.snapshot_type), (2, 'correction'))
        education = application.facts.filter(fact_type='education')
        self.assertEqual(education.count(), 2)
        new = education.exclude(review_status=FactReviewStatus.SUPERSEDED).get()
        self.assertEqual(new.value['program'], 'MA Mathematics Education')
        self.assertEqual(new.supersedes.review_status, FactReviewStatus.SUPERSEDED)
        self.assertEqual(original.versions.count(), 2)  # original kept

    def test_correction_window_and_exception(self):
        application = self.submitted()
        request = services.request_correction(self.hr, application, message='Fix PDS',
                                              document_types=[self.pds],
                                              deadline_at=self.deadline())
        type(request).objects.filter(pk=request.pk).update(
            deadline_at=timezone.now() - timedelta(minutes=1))
        with self.assertRaises(ValidationError):
            documents.upload(self.applicant, application, self.pds, pdf('late.pdf'),
                             replaces=Document.objects.get())
        with self.assertRaises(ValidationError):
            services.grant_correction_exception(self.hr, request, deadline_at=self.deadline(),
                                                reason='')
        services.grant_correction_exception(self.hr, request, deadline_at=self.deadline(2),
                                            reason='Applicant hospitalized')
        documents.upload(self.applicant, application, self.pds, pdf('late.pdf', PDF + b'x'),
                         replaces=Document.objects.get())
        self.assertTrue(AuditLog.objects.filter(action='correction.exception_granted',
                                                reason='Applicant hospitalized').exists())

    def test_withdraw_and_authorized_reopen(self):
        application = self.submitted()
        services.request_correction(self.hr, application, message='x', deadline_at=self.deadline())
        services.withdraw(self.applicant, application, reason='Accepted another job')
        application.refresh_from_db()
        self.assertEqual(application.status, ApplicationStatus.WITHDRAWN)
        self.assertIsNotNone(application.withdrawn_at)
        self.assertFalse(application.correction_requests.filter(status='open').exists())
        with self.assertRaises(ValidationError):
            services.withdraw(self.applicant, application)
        with self.assertRaises(ValidationError):
            services.reopen(self.hr, application, reason='')
        with self.assertRaises(PermissionDenied):
            services.withdraw(self.other_applicant, application)
        services.reopen(self.hr, application, reason='Withdrawal sent by mistake')
        application.refresh_from_db()
        self.assertEqual(application.status, ApplicationStatus.UNDER_REVIEW)
        self.assertIsNone(application.withdrawn_at)

    def test_invalid_transitions_are_prevented(self):
        application = self.draft()
        with self.assertRaises(ValidationError):
            services.transition(application, ApplicationStatus.HIRED, actor=self.hr)
        self.complete_profile(self.other_applicant)
        submitted = self.submitted(self.other_applicant)
        with self.assertRaises(ValidationError):
            services.transition(submitted, ApplicationStatus.HIRED, actor=self.hr)


class FactReviewTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.education = self.application.facts.get(fact_type='education')
        self.version = DocumentVersion.objects.get()

    def test_accept_reject_and_relevance(self):
        services.review_fact(self.hr, self.education, decision='accepted')
        self.education.refresh_from_db()
        self.assertEqual(self.education.review_status, 'accepted')
        self.assertEqual(self.education.reviewed_by, self.hr)
        identity = self.application.facts.get(fact_type='identity')
        with self.assertRaises(ValidationError):
            services.review_fact(self.hr, identity, decision='rejected')
        services.review_fact(self.hr, identity, decision='rejected', reason='Name mismatch')
        with self.assertRaises(ValidationError):  # relevance only for experience/training
            services.set_relevance(self.hr, identity, 'relevant')

    def test_correction_keeps_original_with_field_history(self):
        services.link_evidence(self.hr, self.education, self.version, location={'note': 'p.1'})
        new = services.correct_fact(self.hr, self.education,
                                    {**self.education.value, 'institution': 'EVSU Ormoc'},
                                    reason='TOR shows campus')
        self.education.refresh_from_db()
        self.assertEqual(self.education.review_status, FactReviewStatus.SUPERSEDED)
        self.assertEqual(new.supersedes, self.education)
        self.assertEqual(new.value['institution'], 'EVSU Ormoc')
        self.assertEqual(new.evidence.get().document_version, self.version)
        correction = ApplicationCorrection.objects.get()
        self.assertEqual((correction.field_name, correction.old_value, correction.new_value),
                         ('education.institution', 'EVSU', 'EVSU Ormoc'))
        self.assertEqual(correction.actor, self.hr)
        with self.assertRaises(ValidationError):  # superseded facts cannot be changed
            services.correct_fact(self.hr, self.education, {}, reason='again')

    def test_manual_fact_with_evidence(self):
        fact = services.add_manual_fact(
            self.hr, self.application, 'training',
            {'title': 'Outcome-based teaching', 'hours': '24', 'provider': 'CHED',
             'start_date': None, 'end_date': None},
            reason='Certificate in PDS attachment', evidence_version=self.version)
        self.assertEqual(fact.review_status, 'accepted')
        self.assertTrue(fact.value['manual'])
        self.assertEqual(fact.evidence.count(), 1)
        self.complete_profile(self.other_applicant)
        other = self.submitted(self.other_applicant)
        other_version = DocumentVersion.objects.filter(document__application=other).get()
        with self.assertRaises(ValidationError):
            services.link_evidence(self.hr, fact, other_version)

    def test_qualification_outcomes(self):
        standard = self.application.latest_snapshot().requirement_version \
            .qualification_standards.get()
        with self.assertRaises(ValidationError):
            services.record_qualification_outcome(self.hr, self.application, standard,
                                                  outcome='needs_review')
        services.record_qualification_outcome(self.hr, self.application, standard,
                                              outcome='needs_review', reason='TOR unclear')
        services.record_qualification_outcome(self.hr, self.application, standard,
                                              outcome='meets', supporting_facts=[self.education])
        outcomes = services.qualification_outcomes(self.application)
        current = outcomes[str(standard.pk)]
        self.assertEqual(current.value['outcome'], 'meets')
        self.assertEqual(current.value['supporting_fact_ids'], [str(self.education.pk)])
        self.assertEqual(self.application.facts.filter(
            fact_type=FactType.QUALIFICATION_ASSESSMENT).count(), 2)  # history kept
        self.application.refresh_from_db()
        self.assertEqual(self.application.status, ApplicationStatus.SUBMITTED)  # no auto change

    def test_view_only_and_unassigned_staff_cannot_review(self):
        assign_staff(self.hr, self.vacancy, self.dept_head, AssignmentRole.DEPARTMENT_HEAD)
        self.client.force_login(self.dept_head)
        url = reverse('recruitment:application_review', args=[self.application.pk])
        response = self.client.get(url)
        self.assertContains(response, 'view only')
        with self.assertRaises(PermissionDenied):
            services.review_fact(self.dept_head, self.education, decision='accepted')
        self.client.force_login(self.hr2)
        self.assertEqual(self.client.get(url).status_code, 404)
        with self.assertRaises(PermissionDenied):
            services.review_fact(self.hr2, self.education, decision='accepted')

    def test_staff_list_and_search_respect_access(self):
        self.client.force_login(self.hr)
        url = reverse('recruitment:vacancy_applications', args=[self.vacancy.pk])
        self.assertContains(self.client.get(url), '1 result')
        self.assertContains(self.client.get(url, {'q': 'Mathematics'}), '1 result')
        self.assertContains(self.client.get(url, {'q': 'nobody'}), '0 results')
        self.assertContains(self.client.get(url, {'q': self.application.reference}), '1 result')
        self.client.force_login(self.hr2)
        self.assertEqual(self.client.get(url).status_code, 404)


class Stage5GateTests(ApplicationTestCase):
    """Applicant submits; HR requests and verifies a replacement, manually
    reviews facts; the original file and review history stay visible."""

    def test_end_to_end_through_screens(self):
        client = self.client
        client.force_login(self.applicant)
        client.post(reverse('recruitment:apply', args=[self.vacancy.pk]))
        application = Application.objects.get(applicant__user=self.applicant)
        page = reverse('recruitment:my_application', args=[application.pk])
        client.post(reverse('recruitment:upload_document', args=[application.pk]),
                    {'document_type': self.pds.pk, 'file': pdf('pds-blurry.pdf')})
        client.post(reverse('recruitment:submit_application', args=[application.pk]),
                    {'confirm': 'on'})
        client.post(reverse('recruitment:submit_application', args=[application.pk]),
                    {'confirm': 'on'})  # double click
        application.refresh_from_db()
        self.assertEqual(application.status, 'submitted')
        self.assertEqual(application.snapshots.count(), 1)
        self.assertContains(client.get(page), application.reference)

        client.force_login(self.hr)
        review = reverse('recruitment:application_review', args=[application.pk])
        first = DocumentVersion.objects.get()
        client.post(reverse('recruitment:verify_document', args=[application.pk, first.pk]),
                    {'status': 'incomplete', 'notes': 'Blurry scan'})
        client.post(reverse('recruitment:request_correction', args=[application.pk]), {
            'message': 'Please upload a clearer PDS.', 'document_types': [self.pds.pk],
            'deadline_at': timezone.localtime(self.deadline()).strftime('%Y-%m-%dT%H:%M')})
        application.refresh_from_db()
        self.assertEqual(application.status, 'correction_requested')

        client.force_login(self.applicant)
        self.assertContains(client.get(page), 'Please upload a clearer PDS.')
        self.assertNotContains(client.get(page), 'Blurry scan')  # internal note hidden
        client.post(reverse('recruitment:upload_document', args=[application.pk]),
                    {'replaces': first.document_id, 'file': pdf('pds-clear.pdf', PDF + b'2')})
        client.post(reverse('recruitment:resubmit_corrections', args=[application.pk]))
        application.refresh_from_db()
        self.assertEqual(application.status, 'under_review')

        client.force_login(self.hr)
        second = first.document.current_version()
        self.assertEqual(second.version, 2)
        client.post(reverse('recruitment:verify_document', args=[application.pk, second.pk]),
                    {'status': 'verified', 'notes': ''})
        for fact in application.facts.exclude(review_status='superseded'):
            client.post(reverse('recruitment:fact_review', args=[application.pk, fact.pk]),
                        {'decision': 'accepted'})
        self.assertFalse(application.facts.filter(review_status='pending').exists())
        response = client.get(review)
        self.assertContains(response, 'pds-blurry.pdf')  # original still visible
        self.assertContains(response, 'pds-clear.pdf')
        first.refresh_from_db()
        self.assertEqual(first.verifications.get().notes, 'Blurry scan')

    def deadline(self):
        return timezone.now() + timedelta(days=3)
