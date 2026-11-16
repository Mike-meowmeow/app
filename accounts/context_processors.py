from .roles import WORKSPACES, role_names


def workspaces(request):
    """Workspaces the current user may open, for navigation."""
    names = role_names(getattr(request, 'user', None))
    return {
        'user_workspaces': [(url, label) for role, url, label in WORKSPACES if role in names],
        'user_roles': sorted(names),
    }
