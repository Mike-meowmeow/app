"""The staff assistant's endpoints (JSON): `ask` takes a question and returns
a help answer or confirmation cards; `run` carries out one confirmed card;
`feedback` saves "This did not help". The `unanswered` page is the help guide
review of saved questions.

Questions are not logged. Only when ASSISTANT_COLLECT_UNANSWERED is on are
unanswered ones saved, masked and without the asker's identity
(collection.py). Confirmed operations are audited by the services they run."""

import json

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.http import require_http_methods, require_POST

from accounts import roles
from accounts.access import role_required
from audit import services as audit

from . import collection, guide, operations, services
from .models import HelpPhrase, QuestionReason, QuestionStatus, UnansweredQuestion

ASSISTANT_ROLES = (roles.HR, roles.SYSTEM_ADMIN)
SAVED_NOTE = ('This question was saved, without your name, so the help guide can be '
              'improved.')
MAX_QUESTION_CHARACTERS = 500
WINDOW_SECONDS = 600


def _over_limit(user):
    key = f'assistant-questions:{user.pk}'
    cache.add(key, 0, WINDOW_SECONDS)
    try:
        count = cache.incr(key)
    except ValueError:  # expired between add and incr
        cache.set(key, 1, WINDOW_SECONDS)
        count = 1
    return count > settings.ASSISTANT_MAX_QUESTIONS_PER_10_MINUTES


def _body(request):
    try:
        data = json.loads(request.body or b'{}')
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def _question(request):
    """(question, None) or (None, error response); counts toward the rate limit."""
    question = ' '.join(str(_body(request).get('question', '')).split())
    if len(question) < 3:
        return None, JsonResponse({'error': 'Type a question first.'}, status=400)
    if len(question) > MAX_QUESTION_CHARACTERS:
        return None, JsonResponse({'error': f'Keep the question under '
                                            f'{MAX_QUESTION_CHARACTERS} characters.'},
                                  status=400)
    if _over_limit(request.user):
        return None, JsonResponse({'error': 'Too many questions in a short time. Try again '
                                            'in a few minutes.'}, status=429)
    return question, None


@role_required(*ASSISTANT_ROLES)
@require_POST
def ask(request):
    if not settings.ASSISTANT_ENABLED:
        raise Http404
    question, problem = _question(request)
    if problem:
        return problem
    result = services.answer(question, request.user)
    note = result['note']
    if result.get('unanswered') and result['mode'] in ('none', 'search'):
        if collection.save(question, request.user, QuestionReason.NO_MATCH,
                           result.get('closest', ''), result.get('similarity')):
            note = SAVED_NOTE
    return JsonResponse({
        'mode': result['mode'],
        'answer': guide.format_html_light(result['answer']),
        'note': escape(note),
        'feedback': (collection.enabled() and result['mode'] in ('ai', 'search')
                     and bool(result['sources'])),
        'sources': [{'title': escape(a.title), 'html': a.html} for a in result['sources']],
        'cards': [{'summary': escape(c['summary']), 'token': c['token']}
                  for c in result.get('cards', [])],
    })


@role_required(*ASSISTANT_ROLES)
@require_POST
def run(request):
    """Carry out one confirmed operation card."""
    if not settings.ASSISTANT_ENABLED:
        raise Http404
    token = str(_body(request).get('token', ''))
    try:
        result = operations.run(request.user, token)
    except PermissionDenied:
        return JsonResponse({'error': 'You cannot do this, or the card is no longer valid.'},
                            status=403)
    except ValidationError as exc:
        return JsonResponse({'error': ' '.join(exc.messages)}, status=400)
    if 'redirect' in result:
        return JsonResponse({'redirect': result['redirect']})
    return JsonResponse({'html': str(result['html'])})


@role_required(*ASSISTANT_ROLES)
@require_POST
def feedback(request):
    """'This did not help' under an answer: save the question for review."""
    if not settings.ASSISTANT_ENABLED or not collection.enabled():
        raise Http404
    question, problem = _question(request)
    if problem:
        return problem
    titles = _body(request).get('sources') or []
    closest = next((t for t in titles if isinstance(t, str) and guide.by_title(t)), '')
    collection.save(question, request.user, QuestionReason.NOT_HELPFUL, closest)
    return JsonResponse({'message': 'Thank you. The question was saved, without your name, '
                                    'for the help guide review.'})


# --------------------------------------------------------------------------
# Help guide review: questions the assistant could not answer
# --------------------------------------------------------------------------

class LinkForm(forms.Form):
    article = forms.ChoiceField()
    phrase = forms.CharField(max_length=500)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        found, _ = guide.articles()
        self.fields['article'].choices = [(a.title, a.title) for a in found]


def _articles():
    found, _ = guide.articles()
    return [{'title': a.title, 'roles': a.role_label} for a in found]


def _mark(request, question, status):
    questions = collection.same_text(question)
    count = questions.update(status=status, reviewed_by=request.user,
                             reviewed_at=timezone.now())
    audit.record('assistant.questions_reviewed', entity=question,
                 changes={'status': status, 'count': count})
    return count


@role_required(*ASSISTANT_ROLES)
@require_http_methods(['GET', 'POST'])
def unanswered(request):
    collection.purge_expired()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'remove_phrase':
            phrase = get_object_or_404(HelpPhrase, pk=request.POST.get('phrase'))
            audit.record('assistant.phrase_removed', entity=phrase,
                         changes={'article': phrase.article_title})
            phrase.delete()
            messages.success(request, 'Phrase removed.')
            return redirect('assistant:unanswered')
        question = get_object_or_404(UnansweredQuestion, pk=request.POST.get('question'),
                                     status=QuestionStatus.OPEN)
        if action == 'link':
            form = LinkForm(request.POST)
            if not form.is_valid():
                messages.error(request, 'Choose an article and keep the phrase under 500 '
                                        'characters.')
                return redirect('assistant:unanswered')
            text = collection.mask(form.cleaned_data['phrase'])
            try:
                with transaction.atomic():
                    phrase = HelpPhrase.objects.create(
                        article_title=form.cleaned_data['article'], text=text,
                        added_by=request.user)
            except IntegrityError:
                phrase = None
            if phrase:
                audit.record('assistant.phrase_added', entity=phrase,
                             changes={'article': phrase.article_title})
            count = _mark(request, question, QuestionStatus.LINKED)
            messages.success(request, f'Linked to "{form.cleaned_data["article"]}" '
                                      f'({count} question{"s" if count != 1 else ""}). The '
                                      'assistant now matches this wording to that article.')
        elif action in (QuestionStatus.NEW_ARTICLE, QuestionStatus.OUT_OF_SCOPE):
            count = _mark(request, question, action)
            messages.success(request, f'{count} question{"s" if count != 1 else ""} marked '
                                      f'"{QuestionStatus(action).label}".')
        elif action == 'delete':
            questions = collection.same_text(question)
            count = questions.count()
            audit.record('assistant.questions_deleted', entity=question,
                         changes={'count': count})
            questions.delete()
            messages.success(request, f'{count} question{"s" if count != 1 else ""} deleted.')
        else:
            messages.error(request, 'Unknown action.')
        return redirect('assistant:unanswered')

    titles = {a['title'] for a in _articles()}
    reviewed = (UnansweredQuestion.objects.exclude(status=QuestionStatus.OPEN)
                .select_related('reviewed_by').order_by('-reviewed_at')[:50])
    phrases = [(p, p.article_title in titles) for p in HelpPhrase.objects.select_related(
        'added_by')]
    return render(request, 'assistant/unanswered.html', {
        'groups': collection.open_groups(), 'reviewed': reviewed, 'phrases': phrases,
        'articles': _articles(), 'collecting': collection.enabled(),
        'retention_days': settings.ASSISTANT_UNANSWERED_RETENTION_DAYS,
        'open_count': UnansweredQuestion.objects.filter(status=QuestionStatus.OPEN).count(),
    })
