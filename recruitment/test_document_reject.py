"""Step 1 of the qualification-review plan: verification outcomes explained,
and a quick Reject (with a reason) on the review page's Documents card."""

from django.urls import reverse

from documents.models import DocumentVersion, VerificationStatus

from .test_applications import ApplicationTestCase


class DocumentRejectTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.version = DocumentVersion.objects.get()
        self.review_url = reverse('recruitment:application_review', args=[self.application.pk])
        self.verify_url = reverse('recruitment:verify_document',
                                  args=[self.application.pk, self.version.pk])

    def test_verify_page_explains_each_outcome(self):
        self.client.force_login(self.hr)
        page = self.client.get(self.verify_url)
        for text in ('<strong>Verified</strong>: the right document',
                     '<strong>Incomplete</strong>: the right document, but pages',
                     '<strong>Invalid</strong>: the right kind of document',
                     '<strong>Rejected</strong>: irrelevant or unusable'):
            self.assertContains(page, text)

    def test_review_page_has_a_reject_for_pending_documents(self):
        self.client.force_login(self.hr)
        page = self.client.get(self.review_url)
        self.assertContains(page, '<summary class="btn btn-sm btn-outline-danger">Reject</summary>')
        self.assertContains(page, 'name="status" value="rejected"')

    def test_quick_reject_needs_a_reason_and_returns_to_the_review(self):
        self.client.force_login(self.hr)
        next_url = self.review_url + '#documents'
        refused = self.client.post(self.verify_url, {'status': 'rejected', 'notes': '',
                                                     'next': next_url})
        self.assertEqual(refused.status_code, 200)  # verify page with the error
        self.version.refresh_from_db()
        self.assertEqual(self.version.verification_status, VerificationStatus.PENDING)
        done = self.client.post(self.verify_url, {'status': 'rejected', 'notes': 'Blank page',
                                                  'next': next_url}, follow=True)
        self.assertEqual(done.redirect_chain[-1][0], next_url)  # back to the Documents card
        self.assertContains(done, 'pds.pdf: Rejected.')
        self.version.refresh_from_db()
        self.assertEqual(self.version.verification_status, VerificationStatus.REJECTED)
        self.assertNotContains(done, 'name="status" value="rejected"')  # decided: no button

    def test_view_only_staff_get_no_reject(self):
        self.client.force_login(self.approver)
        page = self.client.get(self.review_url)
        self.assertNotContains(page, 'name="status" value="rejected"')
