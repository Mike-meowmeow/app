from urllib.parse import urlencode

from django.shortcuts import redirect
from django.urls import reverse

from .services import needs_privacy_acknowledgment

ALLOWED_PATH_NAMES = ('accounts:privacy', 'accounts:logout')


class PrivacyAcknowledgmentMiddleware:
    """Applicants must acknowledge the current privacy notice version before
    using the system; changing PRIVACY_NOTICE_VERSION asks again."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and request.method == 'GET' \
                and not self._is_exempt(request.path) \
                and needs_privacy_acknowledgment(user):
            query = urlencode({'next': request.get_full_path()})
            return redirect(f"{reverse('accounts:privacy')}?{query}")
        return self.get_response(request)

    @staticmethod
    def _is_exempt(path):
        exempt = [reverse(name) for name in ALLOWED_PATH_NAMES]
        return path in exempt or path.startswith(('/static/', '/admin/'))


# Session key set once the current sign-in passed two-factor checking (or did
# not need it). accounts.views.complete_two_factor_login sets it.
TWO_FACTOR_SESSION_KEY = 'two_factor_ok'


class TwoFactorMiddleware:
    """Backstop for staff two-factor sign-in: a staff session that was opened
    without the second step (for example through the Django admin's own sign-in
    page) is signed out and sent to the staff sign-in page. The check runs once
    per session; afterwards the session flag is enough."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not request.session.get(TWO_FACTOR_SESSION_KEY):
            from django.contrib.auth import logout
            from django.contrib.auth.views import redirect_to_login

            from . import two_factor
            if two_factor.applies_to(user):
                logout(request)
                return redirect_to_login(request.get_full_path(),
                                         reverse('accounts:staff_login'))
            request.session[TWO_FACTOR_SESSION_KEY] = True
        return self.get_response(request)
