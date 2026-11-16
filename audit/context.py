"""Request/command context for audit events (actor, IP address, reason)."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class AuditContext:
    request: object = None
    reason: str = ''

    @property
    def user(self):
        user = getattr(self.request, 'user', None)
        if user is not None and getattr(user, 'is_authenticated', False):
            return user
        return None

    @property
    def ip_address(self):
        return client_ip(self.request)


_current = ContextVar('audit_context', default=AuditContext())


def client_ip(request):
    if request is None:
        return None
    # REMOTE_ADDR only. Behind Nginx, configure a trusted proxy header before
    # reading X-Forwarded-For (Stage 9).
    return request.META.get('REMOTE_ADDR') or None


def current():
    return _current.get()


@contextmanager
def bind_request(request):
    token = _current.set(AuditContext(request=request))
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def audit_reason(reason):
    """Attach a reason to every audit event recorded inside the block."""
    token = _current.set(replace(_current.get(), reason=reason or ''))
    try:
        yield
    finally:
        _current.reset(token)
