from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth import views as auth_views
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from audit import services as audit
from audit.models import AuditLog

from . import roles, services
from .access import role_required
from .forms import (LoginForm, PasswordChangeForm, PasswordResetForm,
                    PrivacyAcknowledgmentForm, RegistrationForm, ResendVerificationForm,
                    SetPasswordForm, UserAccessForm, UserSearchForm)

User = get_user_model()


# --------------------------------------------------------------------------
# Sign in / out and password recovery (FR-001)
# --------------------------------------------------------------------------

class LoginView(auth_views.LoginView):
    template_name = 'accounts/login.html'
    authentication_form = LoginForm
    redirect_authenticated_user = True


class LogoutView(auth_views.LogoutView):
    pass


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
        'acknowledgments': request.user.privacy_acknowledgments.all()[:5]})


# --------------------------------------------------------------------------
# System administration: accounts and roles (FR-002)
# --------------------------------------------------------------------------

@role_required(roles.SYSTEM_ADMIN)
def user_list(request):
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
    form = UserAccessForm(request.POST or None,
                          initial={'roles': current_roles, 'is_active': target.is_active})
    if request.method == 'POST' and form.is_valid():
        try:
            services.update_user_access(
                request.user, target, role_names=form.cleaned_data['roles'],
                is_active=form.cleaned_data['is_active'], reason=form.cleaned_data['reason'])
        except PermissionDenied as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f'Access for {target.username} was updated.')
            return redirect('accounts:user_list')
    events = AuditLog.objects.filter(entity_type=User._meta.label, entity_id=str(target.pk)) \
        .select_related('actor')[:15]
    return render(request, 'accounts/user_access.html', {
        'target': target, 'form': form, 'is_self': is_self, 'events': events,
        'descriptions': roles.ROLE_DESCRIPTIONS})
