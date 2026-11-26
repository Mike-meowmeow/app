"""Staff help assistant, with a fake Ollama service."""

import json
from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from accounts import roles
from accounts.test_stage3 import make_user, seed
from ai_processing.test_matching import fake_vector
from audit.models import AuditLog

from . import guide, services


class FakeOllama:
    def __init__(self, answer='Open the **Document verification queue** and press Verify.',
                 down=False):
        self.answer = answer
        self.down = down
        self.chats = []

    def post(self, path, payload, timeout):
        if self.down:
            raise services.Unavailable('connection refused')
        if path == '/api/embed':
            # Articles are embedded as "Title. text"; the fake compares titles
            # only, so its word-count vectors rank articles predictably.
            return {'embeddings': [fake_vector(t.split('.')[0]) for t in payload['input']]}
        self.chats.append(payload)
        return {'message': {'content': self.answer}}

    def __enter__(self):
        services._vectors.clear()
        self.patch = mock.patch.object(services, '_post', self.post)
        self.patch.start()
        return self

    def __exit__(self, *exc):
        self.patch.stop()
        services._vectors.clear()


class GuideTests(TestCase):
    def test_every_article_has_a_title_roles_and_text(self):
        articles, digest = guide.articles()
        self.assertGreaterEqual(len(articles), 20)
        self.assertEqual(len({a.title for a in articles}), len(articles))
        for article in articles:
            self.assertTrue(article.roles <= {roles.HR, roles.SYSTEM_ADMIN}, article.title)
            self.assertGreater(len(article.text), 100, article.title)
        self.assertEqual(len(digest), 64)

    def test_light_formatting_escapes_everything_else(self):
        self.assertEqual(guide.format_html_light('**Verify** <script>x</script> `code`'),
                         '<strong>Verify</strong> &lt;script&gt;x&lt;/script&gt; <code>code</code>')

    def test_articles_are_filtered_by_role(self):
        hr_titles = {a.title for a in guide.for_roles({roles.HR})}
        admin_titles = {a.title for a in guide.for_roles({roles.SYSTEM_ADMIN})}
        self.assertIn('Verifying uploaded documents', hr_titles)
        self.assertNotIn('Verifying uploaded documents', admin_titles)
        self.assertIn('Creating staff accounts and assigning roles', admin_titles)
        self.assertNotIn('Creating staff accounts and assigning roles', hr_titles)
        self.assertEqual(guide.for_roles({roles.APPLICANT}), [])


@override_settings(ASSISTANT_ENABLED=True, ASSISTANT_MIN_SIMILARITY=0.2,
                   ASSISTANT_MAX_QUESTIONS_PER_10_MINUTES=30)
class AskTests(TestCase):
    def setUp(self):
        seed()  # creates the role groups
        cache.clear()
        self.hr = make_user('hr1', roles.HR)
        self.admin = make_user('admin1', roles.SYSTEM_ADMIN)
        self.url = reverse('assistant:ask')

    def ask(self, question, user=None):
        self.client.force_login(user or self.hr)
        return self.client.post(self.url, json.dumps({'question': question}),
                                content_type='application/json')

    def test_answer_comes_from_the_closest_articles(self):
        with FakeOllama() as fake:
            data = self.ask('Verifying uploaded documents').json()
        self.assertEqual(data['mode'], 'ai')
        self.assertIn('<strong>Document verification queue</strong>', data['answer'])
        self.assertEqual(data['sources'][0]['title'], 'Verifying uploaded documents')
        prompt = fake.chats[0]['messages'][0]['content']
        self.assertIn('ARTICLE: Verifying uploaded documents', prompt)
        self.assertIn('ONLY the help articles', prompt)
        self.assertEqual(fake.chats[0]['messages'][1]['content'],
                         'Verifying uploaded documents')

    def test_model_output_is_escaped(self):
        with FakeOllama(answer='<img src=x onerror=alert(1)> Press Verify.'):
            data = self.ask('How do I verify uploaded documents?').json()
        self.assertNotIn('<img', data['answer'])
        self.assertIn('&lt;img', data['answer'])

    def test_no_close_article_means_no_model_call(self):
        with override_settings(ASSISTANT_MIN_SIMILARITY=0.99), FakeOllama() as fake:
            data = self.ask('What is the weather tomorrow?').json()
        self.assertEqual(data['mode'], 'none')
        self.assertEqual(fake.chats, [])
        self.assertIn('does not seem to cover', data['answer'])

    def test_ollama_down_falls_back_to_word_search(self):
        with FakeOllama(down=True):
            data = self.ask('How do I request a correction from an applicant?').json()
        self.assertEqual(data['mode'], 'search')
        self.assertEqual(data['sources'][0]['title'], 'Requesting corrections from an applicant')

    def test_system_admin_gets_admin_articles_only(self):
        with FakeOllama() as fake:
            data = self.ask('Creating staff accounts and assigning roles', user=self.admin).json()
            self.ask('Verifying uploaded documents', user=self.admin)  # an HR-only topic
        titles = [s['title'] for s in data['sources']]
        self.assertIn('Creating staff accounts and assigning roles', titles)
        hr_only = {a.title for a in guide.for_roles({roles.HR})} -             {a.title for a in guide.for_roles({roles.SYSTEM_ADMIN})}
        prompts = ' '.join(c['messages'][0]['content'] for c in fake.chats)
        self.assertFalse([t for t in hr_only if f'ARTICLE: {t}' in prompts])

    def test_access_and_switch(self):
        for user in (make_user('approver1', roles.APPROVER), make_user('appl', roles.APPLICANT)):
            self.client.force_login(user)
            response = self.client.post(self.url, json.dumps({'question': 'help me'}),
                                        content_type='application/json')
            self.assertEqual(response.status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)  # to staff sign-in
        with override_settings(ASSISTANT_ENABLED=False):
            self.assertEqual(self.ask('How do I verify?').status_code, 404)
        self.client.force_login(self.hr)
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_question_checks_and_rate_limit(self):
        with FakeOllama():
            self.assertEqual(self.ask('').status_code, 400)
            self.assertEqual(self.ask('x' * 501).status_code, 400)
            with override_settings(ASSISTANT_MAX_QUESTIONS_PER_10_MINUTES=2):
                cache.clear()
                self.assertEqual(self.ask('How do I verify documents?').status_code, 200)
                self.assertEqual(self.ask('How do I verify documents?').status_code, 200)
                self.assertEqual(self.ask('How do I verify documents?').status_code, 429)

    def test_questions_are_not_stored(self):
        self.client.force_login(self.hr)  # the sign-in itself is audited
        before = AuditLog.objects.count()
        with FakeOllama():
            self.client.post(self.url, json.dumps({'question': 'How do I verify documents '
                                                               'for Maria Santos?'}),
                             content_type='application/json')
        self.assertEqual(AuditLog.objects.count(), before)

    def test_widget_is_shown_to_hr_and_admins_on_staff_pages_only(self):
        self.client.force_login(self.hr)
        page = self.client.get(reverse('core:workspace_hr'))
        self.assertContains(page, 'id="assistant"')
        self.assertContains(page, 'assistant/assistant.js')
        self.client.force_login(self.admin)
        self.assertContains(self.client.get(reverse('accounts:user_list')), 'id="assistant"')
        approver = make_user('approver2', roles.APPROVER)
        self.client.force_login(approver)
        self.assertNotContains(self.client.get(reverse('core:workspace_approver')),
                               'id="assistant"')
        with override_settings(ASSISTANT_ENABLED=False):
            self.client.force_login(self.hr)
            self.assertNotContains(self.client.get(reverse('core:workspace_hr')),
                                   'id="assistant"')
