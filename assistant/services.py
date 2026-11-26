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
"""

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


def _article_vectors():
    found, digest = guide.articles()
    key = (settings.ASSISTANT_EMBED_MODEL, digest)
    if key not in _vectors:
        vectors = embed([a.search_text for a in found])
        _vectors.clear()
        _vectors[key] = {a.number: v for a, v in zip(found, vectors)}
    return _vectors[key]


def _words(text):
    return {w for w in re.findall(r'[a-z0-9]+', text.lower()) if w not in STOP_WORDS and len(w) > 1}


def search_by_words(question, candidates):
    """Fallback ranking when Ollama is unavailable: shared words, title first."""
    asked = _words(question)
    scored = []
    for article in candidates:
        score = 2 * len(asked & _words(article.title)) + len(asked & _words(article.text))
        if score:
            scored.append((score, article))
    scored.sort(key=lambda pair: -pair[0])
    return [a for _, a in scored[:TOP_ARTICLES]]


def rank(question, candidates):
    """[(similarity, article)] closest first, by meaning."""
    vectors = _article_vectors()
    [asked] = embed([question])
    return sorted(((cosine(asked, vectors[a.number]), a) for a in candidates),
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


def answer(question, user_roles):
    """{'mode', 'answer', 'sources': [Article], 'note'}.

    mode: 'ai' (model answer from sources), 'none' (no article close enough),
    'search' (Ollama unavailable: word-search results only)."""
    candidates = guide.for_roles(user_roles)
    if not candidates:
        return {'mode': 'none', 'answer': 'There are no help articles for your role.',
                'sources': [], 'note': ''}
    try:
        ranked = rank(question, candidates)
    except Unavailable:
        sources = search_by_words(question, candidates)
        return {'mode': 'search', 'sources': sources,
                'answer': ('The assistant is not available right now. These help articles '
                           'may answer your question:' if sources else
                           'The assistant is not available right now, and no help article '
                           'matched your words.'),
                'note': ''}
    sources = [a for score, a in ranked[:TOP_ARTICLES]
               if score >= settings.ASSISTANT_MIN_SIMILARITY]
    if not sources:
        closest = [a for _, a in ranked[:TOP_ARTICLES]]
        return {'mode': 'none', 'sources': closest,
                'answer': ('The staff help guide does not seem to cover this. The closest '
                           'articles are below; for anything else, ask the System '
                           'Administrator.'),
                'note': ''}
    try:
        text = chat(question, sources)
    except Unavailable:
        return {'mode': 'search', 'sources': sources,
                'answer': 'The assistant could not write an answer right now. These help '
                          'articles match your question:', 'note': ''}
    return {'mode': 'ai', 'answer': text, 'sources': sources,
            'note': 'Written by a local AI model from the articles below. Check the source '
                    'before acting.'}
