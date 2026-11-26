"""The staff assistant's endpoints (JSON): `ask` takes a question and returns
a help answer or confirmation cards; `run` carries out one confirmed card.
Questions are not stored or logged; confirmed operations are audited by the
services they run."""

import json

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.utils.html import escape
from django.views.decorators.http import require_POST

from accounts import roles
from accounts.access import role_required

from . import guide, operations, services

ASSISTANT_ROLES = (roles.HR, roles.SYSTEM_ADMIN)
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


@role_required(*ASSISTANT_ROLES)
@require_POST
def ask(request):
    if not settings.ASSISTANT_ENABLED:
        raise Http404
    question = ' '.join(str(_body(request).get('question', '')).split())
    if len(question) < 3:
        return JsonResponse({'error': 'Type a question first.'}, status=400)
    if len(question) > MAX_QUESTION_CHARACTERS:
        return JsonResponse({'error': f'Keep the question under {MAX_QUESTION_CHARACTERS} '
                                      'characters.'}, status=400)
    if _over_limit(request.user):
        return JsonResponse({'error': 'Too many questions in a short time. Try again in a few '
                                      'minutes.'}, status=429)
    result = services.answer(question, request.user)
    return JsonResponse({
        'mode': result['mode'],
        'answer': guide.format_html_light(result['answer']),
        'note': escape(result['note']),
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
