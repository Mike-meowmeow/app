"""'Other supporting documents': a category HR adds to a vacancy as an
optional item, so applicants can attach files outside the standard list."""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.urls import reverse

from documents import services as documents
from documents.models import DocumentType

from . import application_services as services
from . import services as vacancies
from .test_applications import ApplicationTestCase, pdf


class OtherSupportingDocumentsTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.other = DocumentType.objects.get(code='OTHER')
        self.vacancy = self.complete(self.new_vacancy(position_title='Part-Time Instructor - '
                                                                      'Physics'))
        vacancies.add_document_requirement(
            self.hr, self.vacancy, document_type=self.other, is_required=False, max_files=3,
            instructions='Awards, recommendations, or anything else you want HR to see.')
        vacancies.publish(self.hr, self.vacancy)
        self.vacancy.refresh_from_db()

    def test_the_category_is_seeded_and_not_sent_to_ai_extraction(self):
        self.assertEqual(self.other.name, 'Other supporting documents')
        self.assertNotIn('OTHER', settings.AI_EXTRACTION_TYPES)

    def test_optional_up_to_the_limit_and_does_not_block_submission(self):
        application = self.draft()
        documents.upload(self.applicant, application, self.pds, pdf('pds.pdf'))
        self.assertEqual(services.submission_problems(application), [])  # optional
        for n in range(3):
            documents.upload(self.applicant, application, self.other, pdf(f'award{n}.pdf'))
        with self.assertRaises(ValidationError):
            documents.upload(self.applicant, application, self.other, pdf('award3.pdf'))
        services.submit(self.applicant, application)
        self.assertEqual(application.documents.filter(document_type=self.other).count(), 3)

    def test_applicant_page_offers_it_as_optional(self):
        application = self.draft()
        self.client.force_login(self.applicant)
        page = self.client.get(reverse('recruitment:my_application', args=[application.pk]))
        self.assertContains(page, 'Other supporting documents')
        self.assertContains(page, 'Awards, recommendations')
        self.assertContains(page, f'<option value="{self.other.pk}">Other supporting documents')

    def test_not_offered_when_the_vacancy_does_not_list_it(self):
        application = services.start_application(self.applicant, self.published())[0]
        with self.assertRaises(ValidationError):
            documents.upload(self.applicant, application, self.other, pdf('award.pdf'))
