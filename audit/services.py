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


def _json_safe(value):
    if value is None or isinstance(value, (bool, int, float, str, list, dict)):
        return value
    if hasattr(value, 'isoformat'):
        return value.isoformat()
    return str(value)  # Decimal, UUID


SKIPPED_FIELDS = {'id', 'created_at', 'updated_at', 'password'}


def snapshot(instance, fields=None):
    """JSON-safe field values of a model instance (FKs as their ids)."""
    return {
        f.name: _json_safe(f.value_from_object(instance))
        for f in instance._meta.concrete_fields
        if f.name not in SKIPPED_FIELDS and (fields is None or f.name in fields)
    }


def diff(before, after):
    """{field: {'old': ..., 'new': ...}} for fields whose values changed."""
    before = before or {}
    return {k: {'old': before.get(k), 'new': v} for k, v in after.items()
            if before.get(k) != v}


def record(action, *, entity=None, entity_type='', entity_id='', changes=None,
           reason=None, actor=_FROM_CONTEXT, ip_address=_FROM_CONTEXT, job=None):
    """Store one audit event. Actor, IP, and reason default to the current
    request/command context. `job` links the event to an AI job."""
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
        job=job,
    )
