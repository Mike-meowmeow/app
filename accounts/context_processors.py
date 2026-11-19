from django.conf import settings

from .portal import login_url_name
from .roles import WORKSPACES, role_names


def workspaces(request):
    """Workspaces the current user may open, for navigation."""
    names = role_names(getattr(request, 'user', None))
    return {
        'user_workspaces': [(url, label) for role, url, label in WORKSPACES if role in names],
        'user_roles': sorted(names),
        'portal': settings.SITE_PORTAL,
        'portal_login_url': login_url_name(),
    }
