"""Staff help assistant: find the closest help articles, then let a local
language model write a short answer from those articles only.

- Knowledge is the staff help guide (help_guide.md) and nothing else. The
  assistant never reads applicant, application, or account data, and the
  question is not stored.
- Both models run in Ollama on the same server (ASSISTANT_OLLAMA_URL, which
  must be a local address): an embedding model (all-minilm, the same MiniLM
  family as competency matching) ranks the articles, and a small chat model
  (qwen2.5:3b by default) writes the answer. The web process loads no model.
- The articles used are always returned with the answer, so staff can check
  it. If no article is close enough, the model is not asked at all. If Ollama
  is down or switched off, a word search of the guide is shown instead.
- Questions are matched against each article and its "also asked as" phrases
  (from the guide and from reviewers). When ASSISTANT_COLLECT_UNANSWERED is on,
  questions nothing matched are saved, masked, for review (collection.py).
"""

import hashlib
import json
import math
import re
import urllib.error
import urllib.request

from django.conf import settings

from . import guide

STOP_WORDS = {
    'a', 'an', 'and', 'are', 'can', 'do', 'does', 'for', 'how', 'i', 'in', 'is', 'it', 'my',
    'of', 'on', 'or', 'the', 'to', 'what', 'when', 'where', 'who', 'why', 'with', 'you',
    'ang', 'ng', 'sa', 'na', 'mga', 'ko', 'paano',
}
TOP_ARTICLES = 2
# Questions about how to do something get the help answer, never an operation.
HOW_TO = re.compile(r'\s*(how\s+(do|can|could|should|would|to|does)|paano)\b', re.I)

_vectors = {}  # (embed model, guide hash) -> {article number: vector}


class Unavailable(Exception):
    """Ollama is not reachable or returned an error."""


def _post(path, payload, timeout):
    request = urllib.request.Request(
        settings.ASSISTANT_OLLAMA_URL.rstrip('/') + path,
        data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise Unavailable(str(exc)) from exc


def embed(texts):
    data = _post('/api/embed', {'model': settings.ASSISTANT_EMBED_MODEL, 'input': list(texts)},
                 timeout=30)
    vectors = data.get('embeddings')
    if not vectors or len(vectors) != len(texts):
        raise Unavailable('The embedding model returned no vectors.')
    return vectors


def cosine(u, v):
    dot = sum(a * b for a, b in zip(u, v))
    norm = math.sqrt(sum(a * a for a in u)) * math.sqrt(sum(b * b for b in v))
    return dot / norm if norm else 0.0


def phrases():
    """{article title: [other ways of asking]}: the guide's "Also asked as"
    lines plus the phrases reviewers added from unanswered questions."""
    from .models import HelpPhrase
    found, _ = guide.articles()
    result = {a.title: list(a.phrases) for a in found}
    for title, text in HelpPhrase.objects.values_list('article_title', 'text'):
        if title in result and text not in result[title]:
            result[title].append(text)
    return result


def _article_vectors():
    """{article number: [vector of the article, vectors of its phrases...]}."""
    found, digest = guide.articles()
    extra = phrases()
    phrase_digest = hashlib.sha256(json.dumps(extra, sort_keys=True).encode()).hexdigest()
    key = (settings.ASSISTANT_EMBED_MODEL, digest, phrase_digest)
    if key not in _vectors:
        texts, owners = [], []
        for article in found:
            for text in [article.search_text] + extra[article.title]:
                texts.append(text)
                owners.append(article.number)
        vectors = embed(texts)
        by_article = {}
        for number, vector in zip(owners, vectors):
            by_article.setdefault(number, []).append(vector)
        _vectors.clear()
        _vectors[key] = by_article
    return _vectors[key]


def _words(text):
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in STOP_WORDS and len(w) > 1}


def search_by_words(question, candidates):
    """Fallback ranking when Ollama is unavailable: shared words, title first."""
    asked = _words(question)
    extra = phrases()
    scored = []
    for article in candidates:
        said = _words(' '.join(extra.get(article.title, [])))
        score = (2 * len(asked & _words(article.title)) + len(asked & _words(article.text))
                 + 2 * len(asked & said))
        if score:
            scored.append((score, article))
    scored.sort(key=lambda pair: -pair[0])
    return [a for _, a in scored[:TOP_ARTICLES]]


def rank(question, candidates):
    """[(similarity, article)] closest first, by meaning. An article's
    similarity is the best of its text and its "also asked as" phrases."""
    vectors = _article_vectors()
    [asked] = embed([question])
    return sorted(((max(cosine(asked, v) for v in vectors[a.number]), a) for a in candidates),
                  key=lambda pair: -pair[0])


def system_prompt(sources):
    context = '\n\n'.join(f'ARTICLE: {a.title}\n{guide.plain(a.text)}' for a in sources)
    return (
        'You are the help assistant for staff of the EVSU-Ormoc part-time instructor '
        'recruitment system. Answer the question using ONLY the help articles below. '
        'Give the steps in at most five short sentences, using the exact page and button '
        'names from the articles. If the articles do not answer the question, say that the '
        'help guide does not cover it and suggest asking the System Administrator. Never '
        'invent pages, buttons, rules, or numbers. You have no access to any applicant, '
        'application, or account, so never discuss a particular person or case. Answer in '
        'the language of the question.\n\n' + context)


def chat(question, sources):
    data = _post('/api/chat', {
        'model': settings.ASSISTANT_CHAT_MODEL, 'stream': False,
        'options': {'temperature': 0.1, 'num_predict': 220},
        'messages': [{'role': 'system', 'content': system_prompt(sources)},
                     {'role': 'user', 'content': question}]},
        timeout=settings.ASSISTANT_TIMEOUT_SECONDS)
    answer = ' '.join(((data.get('message') or {}).get('content') or '').split())
    if not answer:
        raise Unavailable('The chat model returned no answer.')
    return answer


def plan(question, user_roles):
    """Ask the model whether the message requests one of the operations.
    Returns (tool name, arguments) or None. Only the question and the tool
    descriptions are sent: never any record."""
    from . import operations
    tools = operations.tools_for(user_roles)
    if not tools:
        return None
    data = _post('/api/chat', {
        'model': settings.ASSISTANT_CHAT_MODEL, 'stream': False, 'tools': tools,
        'options': {'temperature': 0},
        'messages': [
            {'role': 'system', 'content': (
                'You turn a staff request into a tool call for the EVSU-Ormoc recruitment '
                'system. Call exactly one tool only when the person asks you to open a page, '
                'show or count their work, list applications, or run qualification checks, '
                'competency matching, or a retry for an application. Copy exactly the words '
                'they typed for the vacancy or application (a reference, an applicant name, '
                'or words from a position title) into the arguments; never invent or complete '
                'a reference. For a how-to question, or anything else, do not call a tool and '
                'reply with the single word HELP.')},
            {'role': 'user', 'content': question}]},
        timeout=settings.ASSISTANT_TIMEOUT_SECONDS)
    calls = (data.get('message') or {}).get('tool_calls') or []
    if not calls:
        return None
    function = calls[0].get('function') or {}
    arguments = function.get('arguments') or {}
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            arguments = {}
    return function.get('name'), arguments if isinstance(arguments, dict) else {}


def answer(question, user):
    """Operation cards for a request, or a help answer.

    Returns a dict with 'mode' ('confirm', 'refused', or a help mode), 'answer',
    'sources', 'note', and for 'confirm' the 'cards' to confirm."""
    from django.core.exceptions import PermissionDenied

    from accounts.roles import role_names

    from . import operations
    user_roles = role_names(user)
    if operations.forbidden_instruction(question):
        result = help_answer(question, user_roles)
        result['answer'] = operations.REFUSAL + ' ' + result['answer']
        result['mode'] = 'refused'
        return result
    if HOW_TO.match(question):  # "how do I ...": always the help answer
        return help_answer(question, user_roles)
    try:
        planned = plan(question, user_roles)
    except Unavailable:
        planned = None
    if planned:
        name, arguments = planned
        try:
            message, cards = operations.prepare(user, name, arguments, question)
        except operations.NotFound as exc:
            return {'mode': 'none', 'answer': str(exc), 'sources': [], 'note': '', 'cards': []}
        except PermissionDenied:
            pass  # not an operation this person may use: answer from the guide
        else:
            return {'mode': 'confirm', 'answer': message, 'sources': [], 'cards': cards,
                    'note': 'Nothing happens until you press Confirm.'}
    return help_answer(question, user_roles)


def match(question, user_roles):
    """The articles that answer a question, without writing an answer.

    Returns (sources, nearest articles, similarity of the nearest or None,
    'meaning' or 'words'). Used by help_answer and the question check command."""
    candidates = guide.for_roles(user_roles)
    if not candidates:
        return [], [], None, 'meaning'
    try:
        ranked = rank(question, candidates)
    except Unavailable:
        sources = search_by_words(question, candidates)
        return sources, sources, None, 'words'
    sources = [a for score, a in ranked[:TOP_ARTICLES]
               if score >= settings.ASSISTANT_MIN_SIMILARITY]
    return sources, [a for _, a in ranked[:TOP_ARTICLES]], ranked[0][0], 'meaning'


def help_answer(question, user_roles):
    """{'mode', 'answer', 'sources': [Article], 'note', 'unanswered',
    'closest', 'similarity'}.

    mode: 'ai' (model answer from sources), 'none' (no article close enough),
    'search' (Ollama unavailable: word-search results only). 'unanswered' is
    True when no article matched: such questions may be saved for review."""
    if not guide.for_roles(user_roles):
        return {'mode': 'none', 'answer': 'There are no help articles for your role.',
                'sources': [], 'note': ''}
    sources, nearest, similarity, how = match(question, user_roles)
    found = {'closest': nearest[0].title if nearest else '', 'similarity': similarity,
             'unanswered': not sources}
    if how == 'words':
        return {'mode': 'search', 'sources': sources, **found,
                'answer': ('The assistant is not available right now. These help articles '
                           'may answer your question:' if sources else
                           'The assistant is not available right now, and no help article '
                           'matched your words.'),
                'note': ''}
    if not sources:
        return {'mode': 'none', 'sources': nearest, **found,
                'answer': ('The staff help guide does not seem to cover this. The closest '
                           'articles are below; for anything else, ask the System '
                           'Administrator.'),
                'note': ''}
    try:
        text = chat(question, sources)
    except Unavailable:
        return {'mode': 'search', 'sources': sources, **found,
                'answer': 'The assistant could not write an answer right now. These help '
                          'articles match your question:', 'note': ''}
    return {'mode': 'ai', 'answer': text, 'sources': sources, **found,
            'note': 'Written by a local AI model from the articles below. Check the source '
                    'before acting.'}
