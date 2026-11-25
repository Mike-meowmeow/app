"""Separation of the public applicant site and the staff site.

Both sites are the same code and database, run as two server processes that
differ only in SITE_PORTAL:

- ``public``: reachable from the internet. Only the pages in PUBLIC_VIEWS exist
  (anything else is a 404) and only applicant accounts may be signed in.
- ``staff``: a separate host name. Everything requires a staff sign-in, and
  applicant-only pages do not exist. Network restriction, if any, is done in
  Nginx (deploy/nginx-staff.conf).
- ``all``: development and tests. Every page exists on one address; each sign-in
  page still accepts only its own kind of account.

Public access is an allowlist, so a page added later stays private until it is
deliberately listed here.
"""

from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import logout
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ImproperlyConfigured
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse

from .roles import is_applicant_account, is_staff_account

ALL = 'all'
PUBLIC = 'public'
STAFF = 'staff'
PORTALS = (ALL, PUBLIC, STAFF)

PASSWORD_RESET_VIEWS = frozenset({
    'accounts:password_reset', 'accounts:password_reset_done',
    'accounts:password_reset_confirm', 'accounts:password_reset_complete',
})

# Pages that only make sense for applicants; absent from the staff site.
APPLICANT_ONLY_VIEWS = frozenset({
    'accounts:login', 'accounts:register', 'accounts:resend_verification',
    'accounts:verify_email',
    'core:workspace_applicant',
    'applicants:profile', 'applicants:edit_personal', 'applicants:add_record',
    'applicants:edit_record', 'applicants:delete_record',
    'recruitment:apply', 'recruitment:my_applications', 'recruitment:my_application',
    'recruitment:upload_document', 'recruitment:remove_document',
    'recruitment:submit_application', 'recruitment:resubmit_corrections',
    'recruitment:withdraw',
})

# Everything the internet can reach. Each page still does its own permission
# and ownership checks.
PUBLIC_VIEWS = APPLICANT_ONLY_VIEWS | PASSWORD_RESET_VIEWS | frozenset({
    'core:home', 'core:dashboard',
    'accounts:logout', 'accounts:privacy', 'accounts:account', 'accounts:password_change',
    'recruitment:public_list', 'recruitment:public_detail',
    'documents:download',
    'notifications:list', 'notifications:open', 'notifications:mark_all_read',
})

# Sign-in steps before a staff session exists. The two-factor pages work only
# after a correct staff password in the same browser session.
STAFF_SIGN_IN_VIEWS = frozenset({'accounts:staff_login', 'accounts:two_factor_verify',
                                 'accounts:two_factor_setup'})
STAFF_ANONYMOUS_VIEWS = PASSWORD_RESET_VIEWS | STAFF_SIGN_IN_VIEWS


def login_url_name():
    return 'accounts:staff_login' if settings.SITE_PORTAL == STAFF else 'accounts:login'


def site_url_for(user):
    """Base address for links emailed to this user: staff links point to the
    staff site, applicant links to the public site."""
    url = settings.STAFF_SITE_URL if is_staff_account(user) else settings.PUBLIC_SITE_URL
    return url.rstrip('/')


def site_parts_for(user):
    """(protocol, domain) of the site this user belongs to."""
    parts = urlsplit(site_url_for(user))
    return parts.scheme, parts.netloc


def account_allowed_here(user):
    """May this account request a password reset on the current site?"""
    if settings.SITE_PORTAL == PUBLIC:
        return is_applicant_account(user)
    if settings.SITE_PORTAL == STAFF:
        return is_staff_account(user)
    return True


class PortalMiddleware:
    """Applies the rules above before any view runs."""

    def __init__(self, get_response):
        self.get_response = get_response
        if settings.PORTAL_ENFORCED:
            if settings.SITE_PORTAL == ALL:
                raise ImproperlyConfigured(
                    'Set SITE_PORTAL to "public" or "staff" when DJANGO_DEBUG is False.')
            if settings.SITE_PORTAL == PUBLIC and not settings.MALWARE_SCAN_COMMAND:
                raise ImproperlyConfigured(
                    'The public site accepts uploads: set MALWARE_SCAN_COMMAND.')

    def __call__(self, request):
        return self.get_response(request)

    def process_view(self, request, view_func, view_args, view_kwargs):
        portal = settings.SITE_PORTAL
        if portal == PUBLIC:
            return self._public(request)
        if portal == STAFF:
            return self._staff(request)
        return None

    @staticmethod
    def _public(request):
        if request.resolver_match.view_name not in PUBLIC_VIEWS:
            raise Http404
        user = request.user
        if user.is_authenticated and not is_applicant_account(user):
            logout(request)
            return redirect('accounts:login')
        return None

    @staticmethod
    def _staff(request):
        name = request.resolver_match.view_name
        user = request.user
        login_url = reverse('accounts:staff_login')
        if not user.is_authenticated:
            if name in STAFF_ANONYMOUS_VIEWS:
                return None
            if name == 'core:home' or name in APPLICANT_ONLY_VIEWS:
                return redirect(login_url)
            return redirect_to_login(request.get_full_path(), login_url)
        if not is_staff_account(user):
            logout(request)
            return redirect(login_url)
        if name in APPLICANT_ONLY_VIEWS:
            raise Http404
        return None
