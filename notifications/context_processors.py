def unread(request):
    """Unread notification count for the navigation bar."""
    user = getattr(request, 'user', None)
    if not getattr(user, 'is_authenticated', False):
        return {}
    from .services import unread_count
    return {'unread_notifications': unread_count(user)}
