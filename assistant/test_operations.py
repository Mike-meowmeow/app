"""Staff assistant operations: confirmation cards, permissions, and what runs."""

import json

from django.core import signing
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse

from accounts import roles
from accounts.test_stage3 import make_user
from ai_processing.models import AIJob, AIScreening, JobStatus, JobType
from audit.models import AuditLog
from recruitment import application_services as applications
from recruitment.models import FactReviewStatus, FactType
from recruitment.test_applications import ApplicationTestCase

from . import operations
from .tests import FakeOllama


@override_settings(ASSISTANT_ENABLED=True, ASSISTANT_MIN_SIMILARITY=0.2,
                   AI_QUALIFICATION_CHECKS_ENABLED=True, AI_COMPETENCY_MATCHING_ENABLED=True)
class OperationTests(ApplicationTestCase):
    def setUp(self):
        super().setUp()
        cache.clear()
        self.application = self.submitted()  # applicant "Appl Santos"
        self.admin = make_user('admin1', roles.SYSTEM_ADMIN)

    def ask(self, question, tool=None, user=None):
        self.client.force_login(user or self.hr)
        with FakeOllama(tool=tool) as fake:
            data = self.client.post(reverse('assistant:ask'), json.dumps({'question': question}),
                                    content_type='application/json').json()
        return data, fake

    def run_card(self, card, user=None):
        self.client.force_login(user or self.hr)
        return self.client.post(reverse('assistant:run'), json.dumps({'token': card['token']}),
                                content_type='application/json')

    def test_open_page_needs_confirmation_then_redirects(self):
        data, fake = self.ask('open the verification queue',
                              tool=('open_page', {'page': 'verification_queue'}))
        self.assertEqual(data['mode'], 'confirm')
        self.assertEqual(fake.chats, [])  # no answer-writing call
        [card] = data['cards']
        self.assertIn('Document verification queue', card['summary'])
        response = self.run_card(card).json()
        self.assertEqual(response['redirect'], reverse('recruitment:verification_queue'))

    def test_the_model_never_sees_records(self):
        _, fake = self.ask('run checks for Santos', tool=('run_qualification_checks',
                                                          {'application': 'Santos'}))
        sent = json.dumps(fake.plans)
        self.assertNotIn(self.application.reference, sent)
        self.assertNotIn(str(self.application.pk), sent)

    def test_run_checks_by_name_after_confirm(self):
        data, _ = self.ask('run qualification checks for Santos',
                           tool=('run_qualification_checks', {'application': 'Santos'}))
        [card] = data['cards']
        self.assertIn(self.application.reference, card['summary'])
        self.assertFalse(AIScreening.objects.exists())  # nothing ran yet
        result = self.run_card(card).json()
        self.assertIn('Qualification checks done', result['html'])
        self.assertTrue(AIScreening.objects.filter(job__application=self.application).exists())
        self.assertTrue(AuditLog.objects.filter(action='assistant.operation_confirmed',
                                                actor=self.hr).exists())

    def test_several_matches_give_one_card_each_and_none_gives_a_message(self):
        second = make_user('other2', roles.APPLICANT, last_name='Santos')
        self.complete_profile(second)
        self.submitted(second)
        data, _ = self.ask('open Santos', tool=('open_application', {'application': 'Santos'}))
        self.assertEqual(len(data['cards']), 2)
        data, _ = self.ask('open Nobody', tool=('open_application', {'application': 'Nobody'}))
        self.assertEqual(data['mode'], 'none')
        self.assertIn('could not find', data['answer'])

    def test_reference_lookup_and_other_hr_cannot_see_it(self):
        reference = self.application.reference
        data, _ = self.ask(f'open {reference}', tool=('open_application',
                                                       {'application': reference}))
        self.assertEqual(len(data['cards']), 1)
        outsider = make_user('hr_outsider', roles.HR)  # not assigned to the vacancy
        data, _ = self.ask(f'open {reference}', tool=('open_application',
                                                       {'application': reference}),
                           user=outsider)
        self.assertEqual(data['mode'], 'none')

    def test_cards_cannot_be_reused_by_others_or_tampered(self):
        data, _ = self.ask('checks for Santos', tool=('run_qualification_checks',
                                                      {'application': 'Santos'}))
        [card] = data['cards']
        other_hr = make_user('hr_other', roles.HR)
        self.assertEqual(self.run_card(card, user=other_hr).status_code, 403)
        forged = signing.dumps({'u': str(self.hr.pk), 'op': 'run_qualification_checks',
                                'p': {'application': str(self.application.pk)}}, salt='other')
        self.assertEqual(self.run_card({'token': forged}).status_code, 403)
        self.assertFalse(AIScreening.objects.exists())

    def test_forbidden_instructions_are_refused_without_asking_the_model_for_a_tool(self):
        data, fake = self.ask('shortlist Santos', tool=('open_application',
                                                        {'application': 'Santos'}))
        self.assertEqual(data['mode'], 'refused')
        self.assertIn('cannot do that for you', data['answer'])
        self.assertEqual(fake.plans, [])
        self.assertEqual(data.get('cards'), [])
        # A question about how to do it is still answered from the guide.
        data, _ = self.ask('How do I shortlist an applicant?')
        self.assertNotEqual(data['mode'], 'refused')

    def test_tools_the_model_invents_or_the_role_lacks_are_ignored(self):
        data, _ = self.ask('hire everyone', tool=('record_decision', {'outcome': 'hired'}))
        self.assertEqual(data['mode'], 'refused')
        data, _ = self.ask('please do it', tool=('record_decision', {'outcome': 'hired'}))
        self.assertNotEqual(data['mode'], 'confirm')
        data, _ = self.ask('run checks', tool=('run_qualification_checks',
                                               {'application': 'Santos'}), user=self.admin)
        self.assertNotEqual(data['mode'], 'confirm')  # System Admin has no application tools
        self.assertEqual([t['function']['name'] for t in operations.tools_for({roles.SYSTEM_ADMIN})],
                         ['open_page'])

    def test_work_summary_and_list(self):
        data, _ = self.ask('show my pending work', tool=('show_work_summary', {}))
        html = self.run_card(data['cards'][0]).json()['html']
        self.assertIn('Documents waiting for verification: <strong>1</strong>', html)
        data, _ = self.ask('list submitted applications',
                           tool=('list_applications', {'status': 'submitted'}))
        html = self.run_card(data['cards'][0]).json()['html']
        self.assertIn(self.application.reference, html)
        self.assertIn('1 application', html)

    def test_matching_and_retry(self):
        education = self.application.facts.get(fact_type=FactType.EDUCATION)
        applications.review_fact(self.hr, education, decision=FactReviewStatus.ACCEPTED)
        data, _ = self.ask('match competencies for Santos',
                           tool=('run_competency_matching', {'application': 'Santos'}))
        result = self.run_card(data['cards'][0]).json()
        self.assertIn('queued', result['html'])
        self.assertTrue(AIJob.objects.filter(job_type=JobType.COMPETENCY_MATCH).exists())
        data, _ = self.ask('retry failed AI for Santos',
                           tool=('retry_failed_ai', {'application': 'Santos'}))
        result = self.run_card(data['cards'][0]).json()
        self.assertIn('no failed AI jobs', result['html'])

    def test_admin_can_open_admin_pages(self):
        data, _ = self.ask('open the audit trail', tool=('open_page', {'page': 'audit_trail'}),
                           user=self.admin)
        result = self.run_card(data['cards'][0], user=self.admin).json()
        self.assertEqual(result['redirect'], reverse('audit:list'))
        data, _ = self.ask('open the verification queue',
                           tool=('open_page', {'page': 'verification_queue'}), user=self.admin)
        self.assertNotEqual(data['mode'], 'confirm')  # an HR page

    def test_an_invented_reference_is_ignored_and_the_typed_name_used(self):
        # Seen with the real model: it filled in an example reference.
        data, _ = self.ask('check if Santos meets the qualifications',
                           tool=('run_qualification_checks', {'application': 'APP-1A2B3C4D'}))
        [card] = data['cards']
        self.assertIn(self.application.reference, card['summary'])
        data, _ = self.ask('check if this person meets the qualifications',
                           tool=('run_qualification_checks', {'application': 'APP-1A2B3C4D'}))
        self.assertEqual(data['mode'], 'none')

    def test_how_to_questions_get_the_help_answer(self):
        # Seen with the real model: "How do I generate the roster?" became open_vacancy.
        data, fake = self.ask('How do I generate the roster?',
                              tool=('open_vacancy', {'vacancy': 'roster'}))
        self.assertNotEqual(data['mode'], 'confirm')
        self.assertEqual(fake.plans, [])
        self.assertTrue(operations.grounded('Carla-Reyes', 'open the application of Carla Reyes'))
        self.assertFalse(operations.grounded('APP-1A2B3C4D', 'check Reyes'))
        self.assertTrue(operations.grounded('APP-Reyes', 'check if Reyes qualifies'))
        data, _ = self.ask('retry the failed AI for Santos',
                           tool=('retry_failed_ai', {'application': 'APP-Santos'}))
        self.assertIn(self.application.reference, data['cards'][0]['summary'])
