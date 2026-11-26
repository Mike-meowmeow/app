"""Saving unanswered questions, the help guide review page, phrases, and the
question check command."""

import json
import tempfile
from datetime import timedelta
from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from accounts import roles
from accounts.test_stage3 import make_user, seed
from audit.models import AuditLog

from . import collection, guide, services
from .models import HelpPhrase, QuestionReason, QuestionStatus, UnansweredQuestion
from .tests import FakeOllama

# Words no article title shares. The fake embedding is a small hashed vector, so
# unrelated words can still score up to about 0.5; these tests use a 0.7 threshold.
UNCOVERED = 'Quokka ladder zebra marmalade'
ARTICLE = 'Verifying uploaded documents'


@override_settings(ASSISTANT_ENABLED=True, ASSISTANT_MIN_SIMILARITY=0.7,
                   ASSISTANT_COLLECT_UNANSWERED=True, ASSISTANT_UNANSWERED_RETENTION_DAYS=90)
class CollectionTests(TestCase):
    def setUp(self):
        seed()
        cache.clear()
        self.hr = make_user('hr1', roles.HR)
        self.admin = make_user('admin1', roles.SYSTEM_ADMIN)
        make_user('appl1', roles.APPLICANT, last_name='Dimaculangan', middle_name='Ylagan')

    def ask(self, question, user=None, **fake):
        self.client.force_login(user or self.hr)
        with FakeOllama(**fake):
            return self.client.post(reverse('assistant:ask'), json.dumps({'question': question}),
                                    content_type='application/json').json()

    def review(self, **data):
        self.client.force_login(self.hr)
        return self.client.post(reverse('assistant:unanswered'), data)

    # Masking -------------------------------------------------------------

    def test_mask_removes_names_references_emails_and_long_numbers(self):
        text = collection.mask('Is Juan Dimaculangan (APP-1A2B3C4D, juan.d@mail.com, '
                               '+63 917 123 4567) done for 2026 and VAC-00AB12CD? ylagan')
        self.assertNotIn('Dimaculangan', text)
        self.assertNotIn('ylagan', text.lower())
        self.assertNotIn('1A2B3C4D', text)
        self.assertNotIn('@', text)
        self.assertNotIn('917', text)
        self.assertIn('[name]', text)
        self.assertIn('[reference]', text)
        self.assertIn('[email]', text)
        self.assertIn('[number]', text)
        self.assertIn('2026', text)  # a year is not personal

    # Saving ----------------------------------------------------------------

    def test_unanswered_question_is_saved_masked_without_the_asker(self):
        data = self.ask(f'{UNCOVERED} like Dimaculangan')
        self.assertEqual(data['mode'], 'none')
        self.assertIn('saved, without your name', data['note'])
        [saved] = UnansweredQuestion.objects.all()
        self.assertEqual(saved.reason, QuestionReason.NO_MATCH)
        self.assertEqual(saved.asker_role, roles.HR)
        self.assertNotIn('Dimaculangan', saved.text)
        self.assertNotIn('hr1', json.dumps([saved.text, saved.asker_role, saved.closest_article]))
        self.assertTrue(saved.closest_article)
        self.assertIsNotNone(saved.similarity)

    def test_answered_refused_and_operation_requests_are_not_saved(self):
        self.ask(ARTICLE)  # answered
        self.ask('shortlist Dimaculangan')  # refused
        self.ask('open the verification queue',
                 tool=('open_page', {'page': 'verification_queue'}))  # a card
        self.assertFalse(UnansweredQuestion.objects.exists())

    def test_nothing_is_saved_when_collection_is_off(self):
        with override_settings(ASSISTANT_COLLECT_UNANSWERED=False):
            data = self.ask(UNCOVERED)
            self.assertNotIn('saved', data['note'])
            self.assertFalse(data['feedback'])
            self.client.force_login(self.hr)
            response = self.client.post(reverse('assistant:feedback'),
                                        json.dumps({'question': ARTICLE}),
                                        content_type='application/json')
            self.assertEqual(response.status_code, 404)
        self.assertFalse(UnansweredQuestion.objects.exists())

    def test_this_did_not_help_saves_the_question(self):
        data = self.ask(ARTICLE)
        self.assertEqual(data['mode'], 'ai')
        self.assertTrue(data['feedback'])
        self.client.force_login(self.hr)
        response = self.client.post(reverse('assistant:feedback'), json.dumps(
            {'question': ARTICLE, 'sources': [ARTICLE, 'Invented title']}),
            content_type='application/json')
        self.assertEqual(response.status_code, 200)
        saved = UnansweredQuestion.objects.get()
        self.assertEqual(saved.reason, QuestionReason.NOT_HELPFUL)
        self.assertEqual(saved.closest_article, ARTICLE)
        response = self.client.post(reverse('assistant:feedback'), json.dumps(
            {'question': 'Another one', 'sources': ['Invented title']}),
            content_type='application/json')
        self.assertEqual(UnansweredQuestion.objects.get(text='Another one').closest_article, '')

    def test_old_questions_are_deleted_after_the_retention_period(self):
        self.ask(UNCOVERED)
        UnansweredQuestion.objects.update(created_at=timezone.now() - timedelta(days=91))
        self.ask('Kangaroo pineapple tuba lagoon')
        self.assertEqual(UnansweredQuestion.objects.count(), 1)
        UnansweredQuestion.objects.update(created_at=timezone.now() - timedelta(days=91))
        self.client.force_login(self.hr)
        self.client.get(reverse('assistant:unanswered'))
        self.assertFalse(UnansweredQuestion.objects.exists())

    # Review page -------------------------------------------------------------

    def test_review_page_groups_identical_questions(self):
        self.ask(UNCOVERED)
        self.ask(UNCOVERED)
        self.ask(UNCOVERED, user=self.admin)
        self.client.force_login(self.hr)
        page = self.client.get(reverse('assistant:unanswered'))
        self.assertContains(page, 'asked 3 times')
        self.assertContains(page, roles.SYSTEM_ADMIN)
        for user in (make_user('approver1', roles.APPROVER), make_user('a2', roles.APPLICANT)):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse('assistant:unanswered')).status_code, 403)

    def test_linking_adds_a_phrase_and_the_next_ask_is_answered(self):
        self.assertEqual(self.ask(UNCOVERED)['mode'], 'none')
        self.ask(UNCOVERED)
        question = UnansweredQuestion.objects.first()
        self.review(action='link', question=question.pk, article=ARTICLE, phrase=question.text)
        phrase = HelpPhrase.objects.get()
        self.assertEqual(phrase.article_title, ARTICLE)
        self.assertEqual(UnansweredQuestion.objects.filter(status=QuestionStatus.LINKED).count(), 2)
        self.assertTrue(AuditLog.objects.filter(action='assistant.phrase_added').exists())
        data = self.ask(UNCOVERED)
        self.assertEqual(data['mode'], 'ai')
        self.assertEqual(data['sources'][0]['title'], ARTICLE)
        # The word search (Ollama down) uses phrases too.
        data = self.ask(UNCOVERED, down=True)
        self.assertEqual(data['sources'][0]['title'], ARTICLE)
        # Linking the same wording again does not fail.
        UnansweredQuestion.objects.create(text=question.text, reason=QuestionReason.NOT_HELPFUL,
                                          asker_role=roles.HR)
        again = UnansweredQuestion.objects.get(status=QuestionStatus.OPEN)
        self.review(action='link', question=again.pk, article=ARTICLE, phrase=question.text)
        self.assertEqual(HelpPhrase.objects.count(), 1)
        # Removing the phrase undoes the match.
        self.review(action='remove_phrase', phrase=phrase.pk)
        self.assertEqual(self.ask(UNCOVERED)['mode'], 'none')

    def test_phrase_is_masked_and_must_name_a_real_article(self):
        self.ask(UNCOVERED)
        question = UnansweredQuestion.objects.get()
        self.review(action='link', question=question.pk, article='Invented', phrase='x')
        self.assertFalse(HelpPhrase.objects.exists())
        self.review(action='link', question=question.pk, article=ARTICLE,
                    phrase='quokka for Dimaculangan')
        self.assertEqual(HelpPhrase.objects.get().text, 'quokka for [name]')

    def test_out_of_scope_new_article_and_delete(self):
        self.ask(UNCOVERED)
        self.ask('Kangaroo pineapple tuba lagoon')
        self.ask('Joke tapir mango')
        first, second, third = UnansweredQuestion.objects.order_by('created_at')
        self.review(action='out_of_scope', question=first.pk)
        self.review(action='new_article', question=second.pk)
        self.review(action='delete', question=third.pk)
        statuses = dict(UnansweredQuestion.objects.values_list('pk', 'status'))
        self.assertEqual(statuses, {first.pk: QuestionStatus.OUT_OF_SCOPE,
                                    second.pk: QuestionStatus.NEW_ARTICLE})
        self.assertEqual(UnansweredQuestion.objects.get(pk=first.pk).reviewed_by, self.hr)
        deleted = AuditLog.objects.get(action='assistant.questions_deleted')
        self.assertNotIn('Joke', json.dumps(deleted.changes))

    # Guide phrases and the check command -------------------------------------

    def test_also_asked_as_line_in_the_guide(self):
        source = ('# Guide\n\n## Ranking ladder\nRoles: HR\nAlso asked as: where are the ranks '
                  '| ladder of ranks\nOpen the roster.\n')
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'help_guide.md'
            path.write_text(source, encoding='utf-8')
            with mock.patch.object(guide, 'GUIDE_PATH', path):
                guide._load.cache_clear()
                [article] = guide.articles()[0]
                self.assertEqual(article.phrases, ('where are the ranks', 'ladder of ranks'))
                self.assertEqual(article.text, 'Open the roster.')
                self.assertEqual(services.phrases(), {'Ranking ladder': list(article.phrases)})
        guide._load.cache_clear()

    def test_check_command(self):
        self.ask(UNCOVERED)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'questions.txt'
            path.write_text(f'# comment\n{ARTICLE} => {ARTICLE}\n{UNCOVERED} => {ARTICLE}\n',
                            encoding='utf-8')
            out = StringIO()
            with FakeOllama() as fake:
                call_command('assistant_check_questions', file=str(path), stdout=out)
            self.assertEqual(fake.chats, [])  # no answers written
            self.assertIn('Answered: 1 of 2.', out.getvalue())
            self.assertIn('Expected article found: 1 of 2.', out.getvalue())
        out = StringIO()
        with FakeOllama():
            call_command('assistant_check_questions', saved=True, stdout=out)
        self.assertIn('NOT ANSWERED', out.getvalue())
        self.assertIn('Answered: 0 of 1.', out.getvalue())
