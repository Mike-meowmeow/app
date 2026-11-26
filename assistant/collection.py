"""Saving questions the assistant could not answer, for the help guide review.

Only when ASSISTANT_COLLECT_UNANSWERED is on. Before saving, the text is
masked: application and vacancy references, email addresses, long numbers
(phone numbers, ID numbers), and any word that is a first, middle, or last
name of an account in the system. The asker's role is kept, not who asked.
Saved questions are deleted after ASSISTANT_UNANSWERED_RETENTION_DAYS.

Masking by account names catches applicant and staff names typed into a
question, but not names of people without an account; reviewers can delete a
question that still shows personal data.
"""

import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone

from accounts import roles

from .models import QuestionReason, QuestionStatus, UnansweredQuestion

REFERENCE = re.compile(r'\b(?:APP|VAC)-[0-9A-F]{4,}\b', re.I)
EMAIL = re.compile(r'[\w.+-]+@[\w-]+(?:\.[\w-]+)+')
NUMBER = re.compile(r'\+?\d[\d\s().-]*\d')
WORD = re.compile(r"[^\W\d_]+(?:['-][^\W\d_]+)*")
MIN_DIGITS = 7  # phone and ID numbers; shorter numbers (years, counts) stay


def enabled():
    return settings.ASSISTANT_COLLECT_UNANSWERED


def account_name_words():
    words = set()
    for names in get_user_model().objects.values_list('first_name', 'middle_name', 'last_name'):
        for name in names:
            words.update(w.lower() for w in WORD.findall(name or '') if len(w) > 1)
    return words


def mask(text, names=None):
    names = account_name_words() if names is None else names
    text = REFERENCE.sub('[reference]', text)
    text = EMAIL.sub('[email]', text)
    text = NUMBER.sub(lambda m: '[number]' if sum(c.isdigit() for c in m.group())
                      >= MIN_DIGITS else m.group(), text)
    text = WORD.sub(lambda m: '[name]' if m.group().lower() in names else m.group(), text)
    return ' '.join(text.split())[:500]


def role_label(user):
    names = roles.role_names(user)
    return ', '.join(r for r in (roles.HR, roles.SYSTEM_ADMIN) if r in names)


def purge_expired():
    cutoff = timezone.now() - timedelta(days=settings.ASSISTANT_UNANSWERED_RETENTION_DAYS)
    deleted, _ = UnansweredQuestion.objects.filter(created_at__lt=cutoff).delete()
    return deleted


def save(question, user, reason, closest='', similarity=None):
    """Save a masked question; returns it, or None when collection is off."""
    if not enabled():
        return None
    purge_expired()
    return UnansweredQuestion.objects.create(
        text=mask(question), reason=reason, asker_role=role_label(user)[:60],
        closest_article=(closest or '')[:200],
        similarity=None if similarity is None else round(similarity, 3))


def open_groups():
    """Open questions grouped by the closest article, identical texts merged:
    [(article title, [{'text', 'count', 'key', 'reasons', 'roles', 'best', 'last'}])]."""
    merged = {}
    for q in UnansweredQuestion.objects.filter(status=QuestionStatus.OPEN).order_by('created_at'):
        group = merged.setdefault(q.text.lower(), {
            'text': q.text, 'count': 0, 'key': q.pk, 'reasons': set(), 'roles': set(),
            'best': None, 'last': q.created_at, 'closest': q.closest_article})
        group['count'] += 1
        group['reasons'].add(QuestionReason(q.reason).label)
        group['roles'].update(r.strip() for r in q.asker_role.split(',') if r.strip())
        if q.similarity is not None and (group['best'] is None or q.similarity > group['best']):
            group['best'] = q.similarity
        group['last'] = q.created_at
    by_article = {}
    for group in merged.values():
        by_article.setdefault(group['closest'], []).append(group)
    for groups in by_article.values():
        groups.sort(key=lambda g: (-g['count'], g['text'].lower()))
    return sorted(by_article.items(), key=lambda item: (-sum(g['count'] for g in item[1]),
                                                       item[0]))


def same_text(question):
    """The open questions with the same text as this one (reviewed together)."""
    return UnansweredQuestion.objects.filter(status=QuestionStatus.OPEN,
                                             text__iexact=question.text)
