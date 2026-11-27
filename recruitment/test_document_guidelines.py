"""Step 4 of the qualification-review plan: guidelines per document type
(HR only), seeded as drafts, edited in the Django admin."""

from io import StringIO

from django.core.management import call_command
from django.urls import reverse

from documents.models import DocumentType

from .test_applications import ApplicationTestCase


class DocumentGuidelinesTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()  # a PDS was uploaded
        self.review_url = reverse('recruitment:application_review', args=[self.application.pk])

    def test_drafts_are_seeded_for_every_type(self):
        for document_type in DocumentType.objects.all():
            self.assertTrue(document_type.guideline_points, document_type.code)
            self.assertFalse(document_type.guidelines_confirmed)

    def test_seed_never_overwrites_hr_edits(self):
        pds = DocumentType.objects.get(code='PDS')
        pds.guidelines = '- Our own rule'
        pds.guidelines_confirmed = True
        pds.save()
        call_command('seed_reference_data', stdout=StringIO())
        pds.refresh_from_db()
        self.assertEqual(pds.guideline_points, ['Our own rule'])

    def test_hr_sees_the_guidelines_of_this_applications_documents(self):
        self.client.force_login(self.hr)
        page = self.client.get(self.review_url)
        self.assertContains(page, 'Document guidelines (what to check)')
        self.assertContains(page, 'Current CSC Form 212 (Revised 2017) with all pages')
        self.assertContains(page, 'draft, HR to confirm')
        self.assertNotContains(page, "Registrar&#x27;s signature")  # no TOR uploaded
        DocumentType.objects.filter(code='PDS').update(guidelines_confirmed=True)
        self.assertNotContains(self.client.get(self.review_url), 'draft, HR to confirm')

    def test_view_only_staff_do_not_see_them(self):
        self.client.force_login(self.approver)
        self.assertNotContains(self.client.get(self.review_url),
                               'Document guidelines (what to check)')
