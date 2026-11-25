import time
from smtplib import SMTPException

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.csrf import csrf_failure as default_csrf_failure
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from audit import services as audit
from audit.models import AuditLog

from audit.context import client_ip

from . import roles, services, two_factor
from .access import role_required
from .forms import (LoginForm, PasswordChangeForm, PasswordResetForm,
                    PrivacyAcknowledgmentForm, RegistrationForm, ResendVerificationForm,
                    SetPasswordForm, StaffAccountForm, StaffLoginForm, TwoFactorCodeForm,
                    TwoFactorResetForm, UserAccessForm, UserSearchForm)
from .middleware import TWO_FACTOR_SESSION_KEY

User = get_user_model()


# --------------------------------------------------------------------------
# Sign in / out and password recovery (FR-001)
# --------------------------------------------------------------------------

class LoginView(auth_views.LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True


class StaffLoginView(LoginView):
    """Password first; staff covered by two-factor sign-in are not signed in
    until they enter a code (or set up their authenticator)."""

    authentication_form = StaffLoginForm
    extra_context = {'staff_login': True}

    def form_valid(self, form):
        user = form.get_user()
        if not two_factor.applies_to(user):
            response = super().form_valid(form)
            self.request.session[TWO_FACTOR_SESSION_KEY] = True
            return response
        self.request.session[TWO_FACTOR_PENDING_KEY] = {
            'user': str(user.pk), 'backend': user.backend,
            'identifier': form.cleaned_data.get('username', '')[:150],
            'next': self.get_success_url(), 'started': time.time(), 'tries': 0,
        }
        if two_factor.is_enrolled(user):
            return redirect('accounts:two_factor_verify')
        return redirect('accounts:two_factor_setup')


# --------------------------------------------------------------------------
# Staff two-factor sign-in (FR-001)
# --------------------------------------------------------------------------

TWO_FACTOR_PENDING_KEY = 'two_factor_pending'
TWO_FACTOR_PENDING_SECONDS = 5 * 60
TWO_FACTOR_MAX_TRIES = 5


def _pending_two_factor(request):
    """(user, data) for a password-checked sign-in waiting for its code, or
    (None, None) when there is none or it has expired."""
    data = request.session.get(TWO_FACTOR_PENDING_KEY)
    if not data or time.time() - data.get('started', 0) > TWO_FACTOR_PENDING_SECONDS:
        request.session.pop(TWO_FACTOR_PENDING_KEY, None)
        return None, None
    user = User.objects.filter(pk=data['user'], is_active=True).first()
    if user is None or not roles.is_staff_account(user):
        request.session.pop(TWO_FACTOR_PENDING_KEY, None)
        return None, None
    return user, data


def _restart_sign_in(request, message):
    request.session.pop(TWO_FACTOR_PENDING_KEY, None)
    messages.warning(request, message)
    return redirect('accounts:staff_login')


def complete_two_factor_login(request, user, data):
    from django.contrib.auth import login
    request.session.pop(TWO_FACTOR_PENDING_KEY, None)
    login(request, user, backend=data['backend'])
    request.session[TWO_FACTOR_SESSION_KEY] = True


def _two_factor_failed(request, user, data):
    """Counted like a wrong password (same per-account and per-address limits),
    plus a limit per password sign-in."""
    from django.contrib.auth.signals import user_login_failed
    user_login_failed.send(sender=__name__, request=request,
                           credentials={'username': data.get('identifier') or user.username})
    data['tries'] = data.get('tries', 0) + 1
    request.session[TWO_FACTOR_PENDING_KEY] = data
    return data['tries'] >= TWO_FACTOR_MAX_TRIES


def _two_factor_locked(request, user, data):
    ip = client_ip(request)
    return any(services.login_locked(identifier, ip, 'staff')
               for identifier in {data.get('identifier') or '', user.username} if identifier)


@never_cache
@require_http_methods(['GET', 'POST'])
def two_factor_verify(request):
    user, data = _pending_two_factor(request)
    if user is None:
        return _restart_sign_in(request, 'Please sign in again.')
    if not two_factor.is_enrolled(user):
        return redirect('accounts:two_factor_setup')
    form = TwoFactorCodeForm(request.POST or None, allow_recovery=True)
    if request.method == 'POST' and form.is_valid():
        if _two_factor_locked(request, user, data):
            return _restart_sign_in(request, 'Too many failed sign-in attempts. Please wait a '
                                             'few minutes and try again.')
        method = two_factor.verify(user, form.cleaned_data['code'])
        if method:
            complete_two_factor_login(request, user, data)
            if method == 'recovery':
                left = two_factor.recovery_codes_left(user)
                messages.warning(request, f'You signed in with a recovery code; {left} left. '
                                          'Generate new codes from My account if you are '
                                          'running low.')
            return redirect(data['next'])
        if _two_factor_failed(request, user, data):
            return _restart_sign_in(request, 'Too many incorrect codes. Please sign in again.')
        form.add_error('code', 'That code is not correct. Codes change every 30 seconds; '
                               'enter the one shown now.')
    return render(request, 'accounts/two_factor_verify.html', {'form': form})


@never_cache
@require_http_methods(['GET', 'POST'])
def two_factor_setup(request):
    """Set up an authenticator: right after the first password sign-in when
    two-factor is required, or from My account when a staff member opts in."""
    if request.user.is_authenticated:
        user, data = request.user, None
        if not roles.is_staff_account(user):
            raise PermissionDenied
    else:
        user, data = _pending_two_factor(request)
        if user is None:
            return _restart_sign_in(request, 'Please sign in again.')
    if two_factor.is_enrolled(user):
        return redirect('accounts:account' if data is None else 'accounts:two_factor_verify')
    device = two_factor.start_setup(user)
    form = TwoFactorCodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        codes = two_factor.confirm_setup(user, form.cleaned_data['code'])
        if codes:
            if data is None:
                request.session[TWO_FACTOR_SESSION_KEY] = True
                next_url = reverse('accounts:account')
            else:
                complete_two_factor_login(request, user, data)
                next_url = data['next']
            return render(request, 'accounts/two_factor_codes.html', {
                'codes': codes, 'next_url': next_url, 'first_time': True})
        if data is not None and _two_factor_failed(request, user, data):
            return _restart_sign_in(request, 'Too many incorrect codes. Please sign in again.')
        form.add_error('code', 'That code does not match. Check that the app shows the code '
                               'for this account and that your phone\'s clock is set '
                               'automatically.')
    uri = two_factor.provisioning_uri(user, device.secret)
    return render(request, 'accounts/two_factor_setup.html', {
        'form': form, 'qr': two_factor.qr_png_data_uri(uri),
        'secret': two_factor.grouped(device.secret), 'account_name': user.username,
        'issuer': settings.TWO_FACTOR_ISSUER, 'required': settings.STAFF_TWO_FACTOR_REQUIRED,
        'signing_in': data is not None})


@never_cache
@login_required
@require_http_methods(['GET', 'POST'])
def two_factor_recovery_codes(request):
    if not roles.is_staff_account(request.user) or not two_factor.is_enrolled(request.user):
        raise PermissionDenied
    form = TwoFactorCodeForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        if two_factor.verify(request.user, form.cleaned_data['code']) == 'app':
            codes = two_factor.regenerate_recovery_codes(request.user)
            return render(request, 'accounts/two_factor_codes.html', {
                'codes': codes, 'next_url': reverse('accounts:account')})
        form.add_error('code', 'That code is not correct.')
    return render(request, 'accounts/simple_form.html', {
        'form': form, 'title': 'New recovery codes', 'submit_label': 'Generate new codes',
        'intro': 'Your current recovery codes stop working and ten new ones are shown once. '
                 'Enter the code from your authenticator app to continue.'})


@role_required(roles.SYSTEM_ADMIN)
@require_POST
def two_factor_reset(request, pk):
    target = get_object_or_404(User, pk=pk)
    form = TwoFactorResetForm(request.POST)
    if not form.is_valid():
        messages.error(request, 'Enter a reason for resetting two-factor sign-in.')
    else:
        try:
            two_factor.reset(request.user, target, form.cleaned_data['reason'])
        except PermissionDenied as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, f'Two-factor sign-in for {target.username} was reset. '
                                      'They set up a new authenticator at their next sign-in.')
    return redirect('accounts:user_access', pk=pk)


def admin_login_redirect(request):
    """The Django admin signs in through the staff sign-in page, so it gets the
    same account checks, limits, and two-factor step."""
    from django.contrib.auth.views import redirect_to_login
    next_url = request.GET.get('next') or ''
    if not url_has_allowed_host_and_scheme(next_url, {request.get_host()},
                                           require_https=request.is_secure()):
        next_url = ''
    next_url = next_url or reverse('admin:index')
    if request.user.is_authenticated:
        # Signed in already: the admin's own permission check decides. Someone
        # without admin access is refused here rather than sent round in a loop.
        if not (request.user.is_active and request.user.is_staff):
            raise PermissionDenied
        return redirect(next_url)
    return redirect_to_login(next_url, reverse('accounts:staff_login'))


def csrf_failure(request, reason=''):
    """A sign-in page left open goes stale when the user signs in or out in
    another tab. Nothing is submitted: reload the page and ask again (or go on
    to the dashboard if already signed in). Other forms keep the normal 403."""
    if request.path in (reverse('accounts:login'), reverse('accounts:staff_login')):
        if not request.user.is_authenticated:
            messages.warning(request, 'This sign-in page had expired. Please sign in again.')
        return redirect(request.get_full_path())
    return default_csrf_failure(request, reason=reason)


class LogoutView(auth_views.LogoutView):
    def post(self, request, *args, **kwargs):
        if roles.is_staff_account(request.user):
            self.next_page = 'accounts:staff_login'
        return super().post(request, *args, **kwargs)


class PasswordResetView(auth_views.PasswordResetView):
    template_name = 'accounts/simple_form.html'
    form_class = PasswordResetForm
    email_template_name = 'accounts/emails/password_reset_email.txt'
    subject_template_name = 'accounts/emails/password_reset_subject.txt'
    success_url = reverse_lazy('accounts:password_reset_done')
    extra_context = {
        'title': 'Reset your password',
        'intro': 'Enter your registered email address. If it matches an active '
                 'account, we will send a single-use reset link.',
        'submit_label': 'Send reset link',
    }


class PasswordResetDoneView(auth_views.PasswordResetDoneView):
    template_name = 'accounts/message.html'
    extra_context = {
        'title': 'Check your email',
        'message': 'If the address matches an active account, a password reset link is '
                   'on its way. The link works once and expires after '
                   f'{settings.PASSWORD_RESET_TIMEOUT // 60} minutes.',
    }


class PasswordResetConfirmView(auth_views.PasswordResetConfirmView):
    template_name = 'accounts/password_reset_confirm.html'
    form_class = SetPasswordForm
    success_url = reverse_lazy('accounts:password_reset_complete')

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            audit.record('account.password_reset', entity=form.user, actor=form.user)
        return response


class PasswordResetCompleteView(auth_views.PasswordResetCompleteView):
    template_name = 'accounts/message.html'
    extra_context = {
        'title': 'Password changed',
        'message': 'Your password has been set. You can now sign in.',
        'show_login': True,
    }


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = 'accounts/simple_form.html'
    form_class = PasswordChangeForm
    success_url = reverse_lazy('accounts:account')
    extra_context = {'title': 'Change password', 'submit_label': 'Change password'}

    def form_valid(self, form):
        with transaction.atomic():
            response = super().form_valid(form)
            audit.record('account.password_changed', entity=form.user)
        messages.success(self.request, 'Your password was changed.')
        return response


# --------------------------------------------------------------------------
# Registration, verification, privacy (FR-003)
# --------------------------------------------------------------------------

@require_http_methods(['GET', 'POST'])
def register(request):
    if request.user.is_authenticated:
        return redirect('core:dashboard')
    form = RegistrationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            user = services.register_applicant(
                username=data['username'], email=data['email'],
                password=data['password1'], first_name=data['first_name'],
                middle_name=data.get('middle_name'), last_name=data['last_name'])
        except IntegrityError:
            form.add_error(None, 'An account with this username or email already exists.')
        else:
            services.send_verification_email(request, user)
            return render(request, 'accounts/message.html', {
                'title': 'Check your email',
                'message': f'We sent a verification link to {user.email}. Open it to '
                           'activate your account, then sign in.',
                'show_resend': True,
            })
    return render(request, 'accounts/register.html', {
        'form': form, 'notice_version': settings.PRIVACY_NOTICE_VERSION})


def verify_email(request, token):
    try:
        services.verify_email(token)
    except services.VerificationError as exc:
        return render(request, 'accounts/message.html', {
            'title': 'Verification link expired' if exc.kind == 'expired'
                     else 'Verification link not valid',
            'message': 'Request a new verification link below.',
            'show_resend': True,
        }, status=400)
    return render(request, 'accounts/message.html', {
        'title': 'Email verified',
        'message': 'Your email address is verified. You can now sign in.',
        'show_login': not request.user.is_authenticated,
    })


@require_http_methods(['GET', 'POST'])
def resend_verification(request):
    form = ResendVerificationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        services.resend_verification(request, form.cleaned_data['email'])
        return render(request, 'accounts/message.html', {
            'title': 'Check your email',
            'message': 'If an unverified account uses that address, a new verification '
                       'link has been sent. Earlier links still expire on schedule.',
        })
    return render(request, 'accounts/simple_form.html', {
        'form': form, 'title': 'Resend verification link', 'submit_label': 'Send link'})


@login_required
@require_http_methods(['GET', 'POST'])
def privacy(request):
    next_url = request.POST.get('next') or request.GET.get('next') or ''
    if not url_has_allowed_host_and_scheme(next_url, {request.get_host()},
                                           require_https=request.is_secure()):
        next_url = ''
    version = settings.PRIVACY_NOTICE_VERSION
    form = PrivacyAcknowledgmentForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        services.acknowledge_privacy(request.user, version)
        messages.success(request, 'Thank you. Your acknowledgment was recorded.')
        return redirect(next_url or 'core:dashboard')
    return render(request, 'accounts/privacy.html', {
        'form': form, 'version': version, 'next': next_url,
        'needs_ack': services.needs_privacy_acknowledgment(request.user)})


@login_required
def account(request):
    return render(request, 'accounts/account.html', {
        'acknowledgments': request.user.privacy_acknowledgments.all()[:5],
        'two_factor': two_factor.status(request.user)
        if roles.is_staff_account(request.user) else None})


# --------------------------------------------------------------------------
# System administration: accounts and roles (FR-002)
# --------------------------------------------------------------------------

@role_required(roles.SYSTEM_ADMIN)
def user_list(request):
    request.session[roles.ACTIVE_WORKSPACE_SESSION_KEY] = roles.SYSTEM_ADMIN
    form = UserSearchForm(request.GET or None)
    users = User.objects.prefetch_related('groups').order_by('last_name', 'first_name',
                                                             'username')
    q = form.cleaned_data.get('q') if form.is_valid() else ''
    if q:
        users = users.filter(Q(username__icontains=q) | Q(email__icontains=q)
                             | Q(first_name__icontains=q) | Q(last_name__icontains=q))
    page = Paginator(users, 25).get_page(request.GET.get('page'))
    return render(request, 'accounts/user_list.html', {'form': form, 'page': page, 'q': q})


@role_required(roles.SYSTEM_ADMIN)
@require_http_methods(['GET', 'POST'])
def user_access(request, pk):
    target = get_object_or_404(User, pk=pk)
    is_self = target.pk == request.user.pk
    current_roles = sorted(target.groups.values_list('name', flat=True))
    form = UserAccessForm(request.POST or None, target=target,
                          initial={'roles': current_roles, 'is_active': target.is_active})
    if request.method == 'POST' and form.is_valid():
        try:
            services.update_user_access(
                request.user, target, role_names=form.cleaned_data['roles'],
                is_active=form.cleaned_data['is_active'], reason=form.cleaned_data['reason'])
        except PermissionDenied as exc:
            form.add_error(None, str(exc))
        except roles.RoleConflict as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, f'Access for {target.username} was updated.')
            return redirect('accounts:user_list')
    events = AuditLog.objects.filter(entity_type=User._meta.label, entity_id=str(target.pk)) \
        .select_related('actor')[:15]
    return render(request, 'accounts/user_access.html', {
        'target': target, 'form': form, 'is_self': is_self, 'events': events,
        'descriptions': roles.ROLE_DESCRIPTIONS,
        'can_invite': target.is_active and roles.is_staff_account(target),
        'two_factor': two_factor.status(target) if roles.is_staff_account(target) else None,
        'reset_form': TwoFactorResetForm()})


def _send_invitation(request, user):
    try:
        services.send_staff_invitation(request.user, user)
    except (SMTPException, OSError):
        messages.warning(request, f'The email to {user.email} could not be sent. Check the '
                                  'mail settings, then use Send set-password link.')
        return False
    return True


@role_required(roles.SYSTEM_ADMIN)
@require_http_methods(['GET', 'POST'])
def staff_create(request):
    form = StaffAccountForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        data = form.cleaned_data
        try:
            user = services.create_staff_account(
                request.user, username=data['username'], email=data['email'],
                first_name=data['first_name'], middle_name=data['middle_name'],
                last_name=data['last_name'], role_names=data['roles'], reason=data['reason'])
        except IntegrityError:
            form.add_error(None, 'An account with this username or email already exists.')
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            if _send_invitation(request, user):
                messages.success(request, f'Staff account created. A set-password link was '
                                          f'sent to {user.email}.')
            return redirect('accounts:user_access', pk=user.pk)
    return render(request, 'form_page.html', {
        'form': form, 'title': 'New staff account', 'submit_label': 'Create account',
        'intro': 'The person receives a link to choose their own password. Applicants '
                 'register themselves on the public site.',
        'cancel_url': 'accounts:user_list'})


@role_required(roles.SYSTEM_ADMIN)
@require_POST
def staff_invite(request, pk):
    target = get_object_or_404(User, pk=pk)
    if _send_invitation(request, target):
        messages.success(request, f'A set-password link was sent to {target.email}.')
    return redirect('accounts:user_access', pk=pk)
