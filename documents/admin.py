from django.contrib import admin

from .models import DocumentType


@admin.register(DocumentType)
class DocumentTypeAdmin(admin.ModelAdmin):
    """Reference data (FR-026). Deactivate instead of deleting."""

    list_display = ('code', 'name', 'max_size_bytes', 'is_active')
    list_filter = ('is_active',)
    search_fields = ('code', 'name')

    def has_delete_permission(self, request, obj=None):
        return False
