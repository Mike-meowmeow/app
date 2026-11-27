"""Evidence links: one link per fact from the Link button; Unlink, with a
reason, replaces the link controls once a document is linked."""

from django.core.exceptions import PermissionDenied, ValidationError
from django.urls import reverse

from audit.models import AuditLog
from documents.models import DocumentVersion, FactEvidence

from . import application_services as services
from .test_applications import ApplicationTestCase


class EvidenceUnlinkTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.education = self.application.facts.get(fact_type='education')
        self.version = DocumentVersion.objects.get()
        self.review_url = reverse('recruitment:application_review', args=[self.application.pk])

    def link(self):
        return services.link_evidence(self.hr, self.education, self.version,
                                      location={'note': 'p. 1'})

    def test_one_link_per_fact(self):
        self.link()
        self.link()  # the same document again changes nothing
        self.assertEqual(self.education.evidence.count(), 1)
        other = FactEvidence.objects.get().document_version
        second = DocumentVersion.objects.create(
            document=other.document, version=2, file_name='pds2.pdf', storage_key='x',
            file_size_bytes=1, mime_type='application/pdf', sha256='0' * 64,
            uploaded_by=self.applicant)
        with self.assertRaises(ValidationError):
            services.link_evidence(self.hr, self.education, second)

    def test_unlink_needs_a_reason_and_is_audited(self):
        link = self.link()
        with self.assertRaises(ValidationError):
            services.unlink_evidence(self.hr, link, reason='  ')
        services.unlink_evidence(self.hr, link, reason='Wrong file chosen')
        self.assertFalse(self.education.evidence.exists())
        entry = AuditLog.objects.get(action='fact.evidence_unlinked')
        self.assertEqual(entry.reason, 'Wrong file chosen')
        self.assertEqual(entry.changes['source_location'], {'note': 'p. 1'})
        self.assertEqual(entry.changes['file'], self.version.file_name)
        self.link()  # can link again after unlinking
        self.assertEqual(self.education.evidence.count(), 1)

    def test_only_reviewers_unlink(self):
        link = self.link()
        with self.assertRaises(PermissionDenied):
            services.unlink_evidence(self.hr2, link, reason='x')
        self.assertTrue(FactEvidence.objects.filter(pk=link.pk).exists())

    def test_review_page_shows_unlink_instead_of_the_link_form(self):
        self.client.force_login(self.hr)
        link_form = reverse('recruitment:fact_evidence', args=[self.application.pk,
                                                               self.education.pk])
        page = self.client.get(self.review_url)
        self.assertContains(page, f'action="{link_form}"')
        link = self.link()
        unlink = reverse('recruitment:fact_unlink',
                         args=[self.application.pk, self.education.pk, link.pk])
        page = self.client.get(self.review_url)
        self.assertNotContains(page, f'action="{link_form}"')
        self.assertContains(page, f'action="{unlink}"')
        self.assertContains(page, 'Why this document is the wrong evidence')
        missing = self.client.post(unlink, {'reason': ''})
        self.assertEqual(missing.status_code, 302)
        self.assertTrue(FactEvidence.objects.filter(pk=link.pk).exists())  # refused
        self.client.post(unlink, {'reason': 'Wrong page'})
        self.assertFalse(FactEvidence.objects.filter(pk=link.pk).exists())
        page = self.client.get(self.review_url)
        self.assertContains(page, f'action="{link_form}"')
        self.assertContains(page, 'No supporting document')  # flag is back

    def test_automatic_profile_links_can_be_unlinked(self):
        link = FactEvidence.objects.create(application_fact=self.education,
                                           document_version=self.version,
                                           source_location={'note': 'attached to this profile '
                                                                    'entry'})
        services.unlink_evidence(self.hr, link, reason='Applicant attached the wrong file')
        self.assertFalse(self.education.evidence.exists())
