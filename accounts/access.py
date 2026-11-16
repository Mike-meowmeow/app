"""View guards. Record-level checks belong in each module's services and
selectors as records are introduced (ownership, vacancy/committee assignment).
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .roles import has_role


def role_required(*roles):
    """Require login and membership in at least one of `roles` (403 otherwise)."""

    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not has_role(request.user, *roles):
                raise PermissionDenied
            return view(request, *args, **kwargs)

        return login_required(wrapped)

    return decorator


def require_owner(user, owner_id):
    """Raise 403 unless `user` owns the record (for applicant-owned records)."""
    if not user.is_authenticated or str(user.pk) != str(owner_id):
        raise PermissionDenied
