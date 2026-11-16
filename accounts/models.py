from django.conf import settings
from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.utils import timezone

from core.models import AppendOnlyModel, BaseModel


def normalize_email_address(email):
    return (email or '').strip().lower()


class AccountManager(UserManager):
    def _create_user(self, username, email, password, **extra_fields):
        return super()._create_user(username, normalize_email_address(email), password,
                                    **extra_fields)

    def get_by_natural_key(self, username):
        return self.get(**{f'{self.model.USERNAME_FIELD}__iexact': username})


class User(BaseModel, AbstractUser):
    """Custom user (conceptual `users`). Django's `password` field holds the
    framework hash; there is no separate password store. Roles are Django
    Groups (Stage 3)."""

    email = models.EmailField('email address', unique=True)
    middle_name = models.CharField(max_length=150, null=True, blank=True)
    department = models.ForeignKey(
        'core.Department', on_delete=models.PROTECT, null=True, blank=True,
        related_name='users',
    )
    email_verified_at = models.DateTimeField(null=True, blank=True)

    objects = AccountManager()

    class Meta:
        db_table = 'users'
        verbose_name = 'user'
        verbose_name_plural = 'users'
        permissions = [('assign_roles', 'Can assign and revoke user roles')]

    def clean(self):
        super().clean()
        self.email = normalize_email_address(self.email)

    def save(self, *args, **kwargs):
        self.email = normalize_email_address(self.email)
        super().save(*args, **kwargs)

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return ' '.join(p for p in parts if p)


class PrivacyAcknowledgment(AppendOnlyModel):
    """A user's acknowledgment of a specific privacy notice version."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
                             related_name='privacy_acknowledgments')
    notice_version = models.CharField(max_length=32)
    acknowledged_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'privacy_acknowledgments'
        ordering = ['-acknowledged_at']
        indexes = [models.Index(fields=['user', 'notice_version'])]

    def __str__(self):
        return f'{self.user} - notice {self.notice_version}'
