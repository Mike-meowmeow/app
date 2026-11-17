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
    """The client's address. X-Forwarded-For is trusted only when the direct
    peer is a configured proxy (TRUSTED_PROXIES); then the right-most address
    that is not itself a trusted proxy is the client (it cannot be spoofed by
    adding entries on the left)."""
    if request is None:
        return None
    from django.conf import settings
    remote = request.META.get('REMOTE_ADDR') or None
    trusted = set(getattr(settings, 'TRUSTED_PROXIES', ()))
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    if remote in trusted and forwarded:
        import ipaddress
        for address in reversed([a.strip() for a in forwarded.split(',') if a.strip()]):
            if address not in trusted:
                try:
                    return str(ipaddress.ip_address(address))
                except ValueError:
                    break  # malformed header: fall back to the proxy address
    return remote


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
