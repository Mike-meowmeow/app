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
