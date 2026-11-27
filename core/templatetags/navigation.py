from django import template

from accounts.roles import ACTIVE_WORKSPACE_SESSION_KEY
from core.breadcrumbs import build_trail

register = template.Library()


@register.inclusion_tag('includes/breadcrumbs.html', takes_context=True)
def breadcrumbs(context):
    request = context.get('request')
    match = getattr(request, 'resolver_match', None)
    if match is None:
        return {'trail': []}
    active_role = request.session.get(ACTIVE_WORKSPACE_SESSION_KEY)
    return {'trail': build_trail(request.user, match.view_name, match.kwargs, active_role)}


@register.simple_tag
def versioned_static(path):
    """Static URL with a content version (?v=...), so a changed script is
    fetched instead of an old cached copy."""
    from core.static_version import versioned_static as _versioned
    return _versioned(path)
