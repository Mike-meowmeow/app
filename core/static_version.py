"""Static file URLs with a content version (?v=...), so browsers fetch a
changed script instead of reusing an old cached copy (Nginx lets browsers
keep static files for 7 days)."""

import hashlib
from functools import lru_cache

from django.templatetags.static import static


@lru_cache(maxsize=None)
def versioned_static(path):
    from django.contrib.staticfiles import finders
    found = finders.find(path)
    if not found:
        return static(path)
    with open(found, 'rb') as f:
        version = hashlib.sha256(f.read()).hexdigest()[:10]
    return f'{static(path)}?v={version}'
