"""Files attached to profile records and copied into applications."""

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from accounts import roles
from accounts.test_stage3 import make_user
from documents import services as documents
from documents import storage
from documents.models import DocumentType, FactEvidence
from recruitment import application_services as applications
from recruitment import services as vacancies
from recruitment.models import FactType
from recruitment.test_applications import ApplicationTestCase, pdf

from . import services
from .models import EducationalRecord, ProfileDocument


class ProfileDocumentTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.record = EducationalRecord.objects.get(applicant__user=self.applicant)
        self.tor = DocumentType.objects.get(code='TOR')
        self.diploma = DocumentType.objects.get(code='DIPLOMA')
        # A vacancy that asks for a PDS and up to two transcripts.
        vacancy = self.complete(self.new_vacancy(position_title='Part-Time Instructor - Physics'))
        vacancies.add_document_requirement(self.hr, vacancy, document_type=self.tor,
                                           is_required=False, max_files=2, instructions='')
        vacancies.publish(self.hr, vacancy)
        vacancy.refresh_from_db()
        self.tor_vacancy = vacancy

    def attach(self, name='tor.pdf', document_type=None, user=None, record=None):
        return services.attach_document(user or self.applicant, 'education',
                                        (record or self.record).pk,
                                        document_type or self.tor, pdf(name))

    def test_attach_stores_a_private_file_with_checks(self):
        document = self.attach()
        self.assertEqual(document.record_id, self.record.pk)
        self.assertTrue(storage.path_for(document.storage_key).exists())
        self.assertTrue(document.storage_key.startswith('profiles/'))
        with self.assertRaises(ValidationError):  # wrong section for this type
            services.attach_document(self.applicant, 'education', self.record.pk,
                                     DocumentType.objects.get(code='PRC'), pdf())
        with self.assertRaises(ValidationError):  # content check, as for applications
            services.attach_document(self.applicant, 'education', self.record.pk, self.tor,
                                     SimpleUploadedFile('x.pdf', b'not a pdf'))

    def test_only_the_owner_can_attach_view_or_remove(self):
        other = make_user('intruder', roles.APPLICANT)
        with self.assertRaises(EducationalRecord.DoesNotExist):
            self.attach(user=other)
        document = self.attach()
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse('applicants:document_file',
                                                 args=[document.pk])).status_code, 404)
        self.client.post(reverse('applicants:remove_document', args=[document.pk]))
        self.assertTrue(ProfileDocument.objects.filter(pk=document.pk).exists())
        self.client.force_login(self.applicant)
        self.assertEqual(self.client.get(reverse('applicants:document_file',
                                                 args=[document.pk])).status_code, 200)

    def test_file_limit_per_entry(self):
        for i in range(3):
            self.attach(f'f{i}.pdf')
        with self.assertRaises(ValidationError):
            self.attach('f4.pdf')

    def test_removing_an_entry_removes_its_files(self):
        document = self.attach()
        path = storage.path_for(document.storage_key)
        with self.captureOnCommitCallbacks(execute=True):
            services.delete_record(self.applicant, 'education', self.record.pk)
        self.assertFalse(ProfileDocument.objects.filter(pk=document.pk).exists())
        self.assertFalse(path.exists())

    def test_starting_an_application_copies_matching_files(self):
        self.attach('tor.pdf')
        self.attach('diploma.pdf', document_type=self.diploma)  # not asked for: stays out
        self.client.force_login(self.applicant)
        self.client.post(reverse('recruitment:apply', args=[self.tor_vacancy.pk]))
        application = self.applicant.applicant_profile.applications.get(vacancy=self.tor_vacancy)
        copies = list(application.documents.all())
        self.assertEqual([d.document_type.code for d in copies], ['TOR'])
        version = copies[0].current_version()
        source = ProfileDocument.objects.get(file_name='tor.pdf')
        self.assertEqual(copies[0].profile_document, source)
        self.assertNotEqual(version.storage_key, source.storage_key)  # separate file
        self.assertEqual(version.sha256, source.sha256)

    def test_import_respects_limits_and_never_duplicates(self):
        for name in ('a.pdf', 'b.pdf', 'c.pdf'):
            self.attach(name)
        application = applications.start_application(self.applicant, self.tor_vacancy)[0]
        imported, skipped = documents.import_profile_documents(self.applicant, application)
        self.assertEqual(len(imported), 2)  # max_files=2 for TOR
        self.assertEqual(len(skipped), 1)
        again, _ = documents.import_profile_documents(self.applicant, application)
        self.assertEqual(again, [])

    def test_submission_links_the_file_as_evidence_for_its_fact(self):
        self.attach('tor.pdf')
        application = applications.start_application(self.applicant, self.tor_vacancy)[0]
        documents.import_profile_documents(self.applicant, application)
        documents.upload(self.applicant, application, self.pds, pdf('pds.pdf'))
        applications.submit(self.applicant, application)
        fact = application.facts.get(fact_type=FactType.EDUCATION)
        link = FactEvidence.objects.get(application_fact=fact)
        self.assertEqual(link.document_version.document.document_type, self.tor)

    def test_removing_the_profile_file_keeps_the_application_copy(self):
        document = self.attach('tor.pdf')
        application = applications.start_application(self.applicant, self.tor_vacancy)[0]
        [version], _ = documents.import_profile_documents(self.applicant, application)
        with self.captureOnCommitCallbacks(execute=True):
            services.remove_document(self.applicant, document.pk)
        version.refresh_from_db()
        self.assertTrue(storage.path_for(version.storage_key).exists())
        self.assertIsNone(version.document.profile_document)

    def test_submitted_applications_do_not_take_new_profile_files(self):
        application = self.submitted()
        self.attach('late.pdf')
        with self.assertRaises(ValidationError):
            documents.import_profile_documents(self.applicant, application)

    def test_orphan_sweep_keeps_profile_files(self):
        from io import StringIO
        from django.core.management import call_command
        from django.test import override_settings
        document = self.attach()
        with override_settings(DOCUMENT_STORAGE_ROOT=self.storage_dir):
            call_command('sweep_orphan_files', '--min-age-hours', '0', '--delete',
                         stdout=StringIO())
        self.assertTrue(storage.path_for(document.storage_key).exists())

    def test_profile_page_shows_files_and_forms(self):
        self.attach('tor.pdf')
        self.client.force_login(self.applicant)
        page = self.client.get(reverse('applicants:profile'))
        self.assertContains(page, 'tor.pdf')
        self.assertContains(page, 'Attach file')
        self.assertContains(page, reverse('applicants:attach_document',
                                          args=['education', self.record.pk]))

    def training_data(self, **extra):
        return {'title': 'Classroom Assessment Seminar', 'provider': 'Sample Office',
                'hours': '8', 'start_date': '2021-10-12', 'end_date': '2021-10-12', **extra}

    def test_add_form_saves_the_entry_with_its_file(self):
        self.client.force_login(self.applicant)
        url = reverse('applicants:add_record', args=['training'])
        page = self.client.get(url)
        self.assertContains(page, 'Supporting file')
        self.assertContains(page, 'enctype="multipart/form-data"')
        training = DocumentType.objects.get(code='TRAINING')
        response = self.client.post(url, self.training_data(**{
            'file-document_type': training.pk, 'file-file': pdf('seminar.pdf')}))
        self.assertEqual(response.status_code, 302)
        document = ProfileDocument.objects.get(file_name='seminar.pdf')
        self.assertEqual(document.record_kind, 'training')
        self.assertEqual(document.document_type, training)

    def test_file_is_optional_in_the_add_form(self):
        self.client.force_login(self.applicant)
        training = DocumentType.objects.get(code='TRAINING')
        response = self.client.post(reverse('applicants:add_record', args=['training']),
                                    self.training_data(**{'file-document_type': training.pk}))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(ProfileDocument.objects.exists())

    def test_rejected_file_saves_neither_entry_nor_file(self):
        from .models import Training
        self.client.force_login(self.applicant)
        training = DocumentType.objects.get(code='TRAINING')
        response = self.client.post(
            reverse('applicants:add_record', args=['training']),
            self.training_data(**{'file-document_type': training.pk,
                                  'file-file': SimpleUploadedFile('bad.pdf', b'not a pdf')}))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'not accepted')
        self.assertFalse(Training.objects.exists())
        self.assertEqual(self.stored_files(), [])

    def test_edit_form_lists_files_and_adds_another(self):
        self.attach('first.pdf')
        self.client.force_login(self.applicant)
        url = reverse('applicants:edit_record', args=['education', self.record.pk])
        self.assertContains(self.client.get(url), 'first.pdf')
        data = {'degree_level': 'master', 'program': 'MA Mathematics', 'institution': 'EVSU',
                'completion_status': 'completed', 'completed_on': '2015-03-01',
                'file-document_type': self.diploma.pk, 'file-file': pdf('diploma.pdf')}
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(ProfileDocument.objects.filter(record_id=self.record.pk).count(), 2)

    def test_attach_through_the_page(self):
        self.client.force_login(self.applicant)
        prefix = str(self.record.pk)
        self.client.post(reverse('applicants:attach_document', args=['education', self.record.pk]),
                         {f'{prefix}-document_type': self.tor.pk,
                          f'{prefix}-file': pdf('page.pdf')})
        self.assertTrue(ProfileDocument.objects.filter(file_name='page.pdf').exists())
