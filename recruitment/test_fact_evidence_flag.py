"""'No supporting document' flag on HR's Application facts (warning only)."""

from django.urls import reverse

from documents.models import DocumentVersion

from . import application_services as services
from .models import FactReviewStatus
from .test_applications import ApplicationTestCase


class FactEvidenceFlagTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.education = self.application.facts.get(fact_type='education')
        self.identity = self.application.facts.get(fact_type='identity')
        self.version = DocumentVersion.objects.get()
        self.review_url = reverse('recruitment:application_review', args=[self.application.pk])

    def test_rule(self):
        self.assertTrue(services.fact_needs_evidence(self.education))
        self.assertFalse(services.fact_needs_evidence(self.identity))  # from the profile
        services.link_evidence(self.hr, self.education, self.version)
        self.assertFalse(services.fact_needs_evidence(self.education))

    def test_accepted_keeps_the_flag_rejected_drops_it(self):
        services.review_fact(self.hr, self.education, decision=FactReviewStatus.ACCEPTED)
        self.education.refresh_from_db()
        self.assertTrue(services.fact_needs_evidence(self.education))  # warn only
        services.change_fact_decision(self.hr, self.education,
                                      decision=FactReviewStatus.REJECTED, reason='Not proven')
        self.education.refresh_from_db()
        self.assertFalse(services.fact_needs_evidence(self.education))

    def test_review_page_shows_the_flag_and_the_count(self):
        self.client.force_login(self.hr)
        page = self.client.get(self.review_url)
        self.assertContains(page, '&#9888; No supporting document</span>', count=1)
        self.assertContains(page, '1 fact has no supporting document')
        self.assertContains(page, 'border-warning border-2')
        self.assertContains(page, '>Accept</button>')  # still allowed
        services.link_evidence(self.hr, self.education, self.version)
        page = self.client.get(self.review_url)
        self.assertNotContains(page, 'No supporting document</span>')
        self.assertNotContains(page, 'has no supporting document')
        self.assertNotContains(page, 'border-warning border-2')
