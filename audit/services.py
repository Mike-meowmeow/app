"""Recording protected business events (FR-017).

Call `record()` inside the same transaction as the business change so both
commit or roll back together.
"""

from .context import current
from .models import AuditLog

SENSITIVE_KEY_PARTS = ('password', 'token', 'secret', 'session', 'key')
REDACTED = '[redacted]'
_FROM_CONTEXT = object()


def redact(value):
    if isinstance(value, dict):
        return {
            k: REDACTED if any(part in str(k).lower() for part in SENSITIVE_KEY_PARTS)
            else redact(v)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


def entity_label(obj):
    return obj._meta.label


def record(action, *, entity=None, entity_type='', entity_id='', changes=None,
           reason=None, actor=_FROM_CONTEXT, ip_address=_FROM_CONTEXT):
    """Store one audit event. Actor, IP, and reason default to the current
    request/command context."""
    ctx = current()
    if entity is not None:
        entity_type = entity_label(entity)
        entity_id = str(entity.pk)
    if actor is _FROM_CONTEXT:
        actor = ctx.user
    if actor is not None and not getattr(actor, 'is_authenticated', False):
        actor = None
    if ip_address is _FROM_CONTEXT:
        ip_address = ctx.ip_address
    return AuditLog.objects.create(
        actor=actor,
        action=action,
        entity_type=entity_type[:100],
        entity_id=str(entity_id)[:64],
        changes=redact(changes or {}),
        reason=reason if reason is not None else ctx.reason,
        ip_address=ip_address,
    )
