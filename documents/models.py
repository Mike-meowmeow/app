from django.db import models
from django.db.models import Q

from core.models import BaseModel

DEFAULT_MIME_TYPES = ['application/pdf', 'image/jpeg', 'image/png']
DEFAULT_MAX_SIZE = 10 * 1024 * 1024


def default_mime_types():
    return list(DEFAULT_MIME_TYPES)


class DocumentType(BaseModel):
    """Configurable document category (PDS, transcript, certificate, ...).
    Deactivate instead of deleting once referenced."""

    code = models.CharField(max_length=32, unique=True)
    name = models.CharField(max_length=150)
    allowed_mime_types = models.JSONField(default=default_mime_types)
    max_size_bytes = models.PositiveBigIntegerField(default=DEFAULT_MAX_SIZE)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'document_types'
        ordering = ['name']
        constraints = [
            models.CheckConstraint(condition=Q(max_size_bytes__gt=0),
                                   name='document_type_max_size_positive'),
        ]

    def __str__(self):
        return self.name
