"""Step 3 of the qualification-review plan: short links to the related
documents under each standard, and a Supporting documents line."""

from types import SimpleNamespace

from django.urls import reverse

from documents.models import Document, DocumentVersion

from . import review_gate
from .test_applications import ApplicationTestCase


class StandardDocumentsTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()  # only a PDS was uploaded
        self.pds_version = DocumentVersion.objects.get()
        self.standard = self.application.latest_snapshot().requirement_version \
            .qualification_standards.get()  # education
        document = Document.objects.create(application=self.application,
                                           document_type=self.tor, uploaded_by=self.applicant)
        self.tor_version = DocumentVersion.objects.create(
            document=document, version=1, file_name='tor.pdf', storage_key='x',
            file_size_bytes=1, mime_type='application/pdf', sha256='0' * 64,
            uploaded_by=self.applicant)

    def test_mapping_and_supporting(self):
        other = SimpleNamespace(pk='other-1', criterion_type='other')
        by_standard, supporting = review_gate.documents_by_standard(
            self.application, [self.standard, other])
        self.assertEqual(by_standard[str(self.standard.pk)], [self.tor_version])  # TOR only
        self.assertEqual(set(by_standard['other-1']), {self.tor_version, self.pds_version})
        self.assertEqual(supporting, [self.pds_version])  # PDS fits no criterion

    def test_review_page_shows_the_links_with_status(self):
        self.client.force_login(self.hr)
        page = self.client.get(reverse('recruitment:application_review',
                                       args=[self.application.pk]))
        tor_link = reverse('documents:download', args=[self.tor_version.pk]) + '?inline=1'
        self.assertContains(page, f'href="{tor_link}" target="_blank" rel="noopener">'
                                  'Transcript of Records v1</a>')
        self.assertContains(page, '(Pending Verification)')
        self.assertContains(page, 'Supporting documents:')
        self.assertContains(page, 'Personal Data Sheet (CSC Form 212) v1</a>')
