"""The staff help guide (help_guide.md) as articles the assistant answers from."""

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from django.utils.html import escape

from accounts import roles

GUIDE_PATH = Path(__file__).with_name('help_guide.md')
ROLE_NAMES = {'HR': roles.HR, 'System Administrator': roles.SYSTEM_ADMIN}


@dataclass(frozen=True)
class Article:
    number: int
    title: str
    roles: frozenset
    text: str

    @property
    def search_text(self):
        return f'{self.title}. {plain(self.text)}'

    @property
    def html(self):
        return format_html_light(self.text)


def plain(text):
    return re.sub(r'\*\*|`', '', text)


def format_html_light(text):
    """Escape, then allow only **bold** and `code` from the guide's markdown."""
    html = escape(text)
    html = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', html)
    return re.sub(r'`(.+?)`', r'<code>\1</code>', html)


@lru_cache(maxsize=1)
def _load(mtime):
    source = GUIDE_PATH.read_text(encoding='utf-8')
    articles = []
    for number, block in enumerate(re.split(r'^## ', source, flags=re.M)[1:]):
        title, _, body = block.partition('\n')
        match = re.match(r'Roles:\s*(.+)\n', body)
        if not match:
            raise ValueError(f'Help article "{title.strip()}" has no Roles: line.')
        names = frozenset(ROLE_NAMES[r.strip()] for r in match.group(1).split(','))
        text = ' '.join(body[match.end():].split())
        articles.append(Article(number, title.strip(), names, text))
    return tuple(articles), hashlib.sha256(source.encode()).hexdigest()


def articles():
    """(articles, guide hash); reloaded when the file changes."""
    return _load(GUIDE_PATH.stat().st_mtime)


def for_roles(user_roles):
    found, _ = articles()
    return [a for a in found if a.roles & set(user_roles)]
