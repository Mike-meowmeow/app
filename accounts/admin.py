from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin
from django.contrib.auth.forms import UserChangeForm

from . import roles
from .models import PrivacyAcknowledgment, User


class UserForm(UserChangeForm):
    def clean(self):
        data = super().clean()
        if 'groups' in data:
            current = self.instance.groups.values_list('name', flat=True)
            roles.validate_role_change(current, [g.name for g in data['groups']],
                                       is_superuser=data.get('is_superuser', False))
        return data


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    form = UserForm
    fieldsets = (
        (None, {'fields': ('username', 'password')}),
        ('Personal info', {'fields': ('first_name', 'middle_name', 'last_name', 'email',
                                      'email_verified_at', 'department')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser',
                                    'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'password1', 'password2'),
        }),
    )
    list_display = ('username', 'email', 'first_name', 'last_name', 'department',
                    'is_active', 'is_staff')
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'department', 'groups')
    search_fields = ('username', 'email', 'first_name', 'middle_name', 'last_name')


@admin.register(PrivacyAcknowledgment)
class PrivacyAcknowledgmentAdmin(admin.ModelAdmin):
    """Read-only: acknowledgments are recorded by the application."""

    list_display = ('user', 'notice_version', 'acknowledged_at')
    search_fields = ('user__username', 'user__email', 'notice_version')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
