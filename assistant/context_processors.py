from django.conf import settings

from accounts import roles
from accounts.portal import PUBLIC

from .views import ASSISTANT_ROLES


def assistant(request):
    """Show the floating assistant to HR and System Administrators on staff pages."""
    user = getattr(request, 'user', None)
    visible = (settings.ASSISTANT_ENABLED and settings.SITE_PORTAL != PUBLIC
               and roles.has_role(user, *ASSISTANT_ROLES))
    return {'assistant_visible': visible}
