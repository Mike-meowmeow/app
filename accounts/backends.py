from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
from django.db.models import Q


class EmailOrUsernameBackend(ModelBackend):
    """Log in with username or email (case-insensitive) and password.

    Inactive accounts are refused here and, through ModelBackend.get_user, on
    every later request, so disabling an account ends existing sessions.
    """

    def authenticate(self, request, username=None, password=None, **kwargs):
        UserModel = get_user_model()
        if username is None:
            username = kwargs.get(UserModel.USERNAME_FIELD)
        if not username or password is None:
            return None
        identifier = username.strip()
        candidates = list(UserModel._default_manager.filter(
            Q(username__iexact=identifier) | Q(email__iexact=identifier)))
        if not candidates:
            # Run the hasher anyway so response time doesn't reveal whether
            # the account exists.
            UserModel().set_password(password)
            return None
        # Prefer an exact username match if an identifier matches two accounts.
        candidates.sort(key=lambda u: u.username.lower() != identifier.lower())
        for user in candidates:
            if user.check_password(password) and self.user_can_authenticate(user):
                return user
        return None
