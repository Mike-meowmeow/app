"""A recorded qualification outcome is locked ("Outcome recorded") until
something it was based on changes; a mistake is fixed with Change outcome,
which needs a reason."""

from django.core.exceptions import ValidationError
from django.urls import reverse

from audit.models import AuditLog
from notifications.models import Notification

from . import application_services as services
from .test_applications import ApplicationTestCase


class OutcomeLockTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        self.application = self.submitted()
        self.education = self.application.facts.get(fact_type='education')
        self.standard = self.application.latest_snapshot().requirement_version \
            .qualification_standards.get()
        self.review_url = reverse('recruitment:application_review', args=[self.application.pk])
        self.outcome_url = reverse('recruitment:qualification_outcome',
                                   args=[self.application.pk, self.standard.pk])
        self.open_review(self.application)  # the review opens only when everything is decided

    def new_information(self):
        """HR adds a fact found in a document: the outcome's inputs change."""
        return services.add_manual_fact(
            self.hr, self.application, 'training',
            {'title': 'Outcome-based teaching', 'hours': '8', 'provider': 'CHED',
             'start_date': None, 'end_date': None}, reason='Certificate in the PDS')

    def record(self, **kwargs):
        kwargs.setdefault('outcome', 'needs_review')
        kwargs.setdefault('reason', 'TOR unclear')
        return services.record_qualification_outcome(self.hr, self.application, self.standard,
                                                     **kwargs)

    def current(self):
        return services.qualification_outcomes(self.application)[str(self.standard.pk)]

    def test_recorded_outcome_is_locked_until_its_inputs_change(self):
        self.assertFalse(services.outcome_locked(self.application, None))
        self.record()
        self.assertTrue(services.outcome_locked(self.application, self.current()))
        with self.assertRaises(ValidationError):
            self.record(outcome='meets')
        # New information after the outcome.
        self.new_information()
        self.assertFalse(services.outcome_locked(self.application, self.current()))
        self.record(outcome='meets', supporting_facts=[self.education])  # no reason needed
        self.assertEqual(self.current().value['outcome'], 'meets')
        self.assertTrue(services.outcome_locked(self.application, self.current()))

    def test_change_outcome_needs_a_reason_is_audited_and_notifies(self):
        self.record()
        self.record(outcome='meets', reason='', change_reason='Picked the wrong option')
        current = self.current()
        self.assertEqual(current.value['outcome'], 'meets')
        self.assertEqual(current.value['change_reason'], 'Picked the wrong option')
        self.assertTrue(AuditLog.objects.filter(action='qualification.outcome_changed',
                                                reason='Picked the wrong option').exists())
        [note] = Notification.objects.filter(title='Review decision changed')
        self.assertEqual(note.user, self.approver)
        self.assertIn('from Needs Review to Meets', note.message)
        self.assertNotIn('wrong option', note.message)

    def test_outcomes_recorded_before_the_lock_count_as_locked(self):
        fact = self.record()
        fact.value.pop('inputs_hash')
        self.assertTrue(services.outcome_locked(self.application, fact))

    def test_review_page_shows_outcome_recorded_and_change_outcome(self):
        self.client.force_login(self.hr)
        page = self.client.get(self.review_url)
        self.assertContains(page, '>Record outcome</a>')
        self.assertNotContains(page, 'Outcome recorded')
        self.record()
        page = self.client.get(self.review_url)
        self.assertContains(page, '&#10003; Outcome recorded</button>')
        self.assertContains(page, self.outcome_url + '?change=1')
        self.assertNotContains(page, '>Record outcome</a>')
        self.new_information()
        page = self.client.get(self.review_url)
        self.assertContains(page, 'Record outcome again')
        self.assertContains(page, 'New information since the outcome')
        self.assertNotContains(page, 'Outcome recorded</button>')

    def test_outcome_form_is_closed_while_locked_and_change_asks_for_a_reason(self):
        self.record()
        self.client.force_login(self.hr)
        response = self.client.get(self.outcome_url)
        self.assertRedirects(response, self.review_url + '#qualifications',
                             fetch_redirect_response=False)
        form = self.client.get(self.outcome_url + '?change=1')
        self.assertContains(form, 'Why the recorded outcome was a mistake')
        self.assertContains(form, 'Change outcome')
        missing = self.client.post(self.outcome_url + '?change=1', {'outcome': 'meets'})
        self.assertEqual(missing.status_code, 200)  # reason missing: form shown again
        self.assertEqual(self.current().value['outcome'], 'needs_review')
        done = self.client.post(self.outcome_url + '?change=1',
                                {'outcome': 'meets', 'change_reason': 'Wrong option chosen'})
        self.assertEqual(done.status_code, 302)
        self.assertEqual(self.current().value['outcome'], 'meets')
        page = self.client.get(self.review_url)
        self.assertContains(page, 'Changed: Wrong option chosen')
