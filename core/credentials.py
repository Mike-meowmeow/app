"""The reference list of licenses (PRC) and eligibilities (CSC).

- names(kind): the active names, for suggestions while typing.
- canonical(kind, text): the listed name for a name or one of its aliases
  (ignoring case, spacing, and punctuation), or None when it is not listed.
- digest(): a hash of the list, so advisory results that used it show as out
  of date when a System Administrator changes it.
- CredentialNameInput: a text box with suggestions (an HTML datalist). Any
  name can still be typed, for credentials not on the list.
"""

import hashlib
import json
import re

from django import forms
from django.templatetags.static import static
from django.utils.html import escape, format_html_join
from django.utils.safestring import mark_safe

from .models import Credential, CredentialKind


def _key(text):
    return ' '.join(re.findall(r'[a-z0-9]+', (text or '').lower()))


def names(kind):
    return list(Credential.objects.filter(kind=kind, is_active=True)
                .order_by('name').values_list('name', flat=True))


def lookup(kind):
    """{normalized name or alias: listed name} for the active entries."""
    found = {}
    for credential in Credential.objects.filter(kind=kind, is_active=True):
        for text in [credential.name] + credential.alias_list():
            found.setdefault(_key(text), credential.name)
    return found


def canonical(kind, text, table=None):
    key = _key(text)
    if not key:
        return None
    return (lookup(kind) if table is None else table).get(key)


def digest():
    rows = list(Credential.objects.filter(is_active=True).order_by('kind', 'name')
                .values_list('kind', 'name', 'aliases'))
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()[:16]


def search(kind, query, limit=20):
    """Active entries whose name or an alias contains the query."""
    key = _key(query)
    result = []
    for credential in Credential.objects.filter(kind=kind, is_active=True).order_by('name'):
        texts = [credential.name] + credential.alias_list()
        if not key or any(key in _key(t) for t in texts):
            result.append({'name': credential.name, 'aliases': credential.alias_list()})
            if len(result) >= limit:
                break
    return result


class CredentialNameInput(forms.TextInput):
    """A text box with the listed names as suggestions.

    kinds: one kind, or several; with several, `switch` names the form field
    (e.g. criterion_type) whose value picks the list (static/core/credentials.js).
    """

    def __init__(self, kinds, switch=None, attrs=None):
        self.kinds = [kinds] if isinstance(kinds, str) else list(kinds)
        self.switch = switch
        super().__init__(attrs)

    def get_context(self, name, value, attrs):
        context = super().get_context(name, value, attrs)
        widget_attrs = context['widget']['attrs']
        base = widget_attrs.get('id') or f'id_{name}'
        widget_attrs['list'] = f'{base}-{self.kinds[0]}-names'
        widget_attrs['autocomplete'] = 'off'
        if self.switch:
            widget_attrs['data-credential-switch'] = self.switch
            widget_attrs['data-credential-base'] = base
        return context

    def render(self, name, value, attrs=None, renderer=None):
        html = super().render(name, value, attrs, renderer)
        base = (attrs or {}).get('id') or self.attrs.get('id') or f'id_{name}'
        lists = []
        for kind in self.kinds:
            options = format_html_join('', '<option value="{}"></option>',
                                       ((n,) for n in names(kind)))
            lists.append(f'<datalist id="{escape(base)}-{kind}-names">{options}</datalist>')
        script = (f'<script src="{escape(static("core/credentials.js"))}" defer></script>'
                  if self.switch else '')
        return mark_safe(html + ''.join(lists) + script)


KINDS = (CredentialKind.LICENSE, CredentialKind.ELIGIBILITY)
