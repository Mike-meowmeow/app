from django import forms
from django.contrib.auth import forms as auth_forms
from django.contrib.auth import get_user_model
from django.contrib.auth.signals import user_login_failed
from django.urls import reverse
from django.utils.html import format_html

from core.forms import BootstrapFormMixin

from . import portal, roles
from .services import requires_email_verification

User = get_user_model()


class LoginForm(BootstrapFormMixin, auth_forms.AuthenticationForm):
    """Applicant sign-in. StaffLoginForm is the staff equivalent."""

    audience = 'applicant'

    username = forms.CharField(label='Username or email', max_length=254,
                               widget=forms.TextInput(attrs={'autofocus': True,
                                                             'autocomplete': 'username'}))

    error_messages = {
        # One message for unknown accounts, wrong passwords, and disabled
        # accounts, so the response never reveals whether an account exists.
        'invalid_login': 'Incorrect username/email or password.',
        'inactive': 'Incorrect username/email or password.',
    }

    def clean(self):
        from audit.context import client_ip
        from .services import login_locked
        identifier = (self.cleaned_data.get('username') or '').strip()
        if identifier and login_locked(identifier, client_ip(self.request), self.audience):
            raise forms.ValidationError(
                'Too many failed sign-in attempts. Please wait a few minutes and try again, '
                'or reset your password.', code='throttled')
        return super().clean()

    def account_belongs_here(self, user):
        return roles.is_applicant_account(user)

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not self.account_belongs_here(user):
            # Same answer as a wrong password, and counted like one, so this
            # page never reveals that an account exists on the other site.
            user_login_failed.send(sender=__name__, request=self.request,
                                   credentials={'username': self.cleaned_data.get('username')})
            raise self.get_invalid_login_error()
        if requires_email_verification(user):
            raise forms.ValidationError(
                format_html('Please verify your email address before signing in. '
                            '<a href="{}">Send a new verification link</a>.',
                            reverse('accounts:resend_verification')),
                code='unverified')


class StaffLoginForm(LoginForm):
    audience = 'staff'

    def account_belongs_here(self, user):
        return roles.is_staff_account(user)


class RegistrationForm(BootstrapFormMixin, auth_forms.BaseUserCreationForm):
    """Public applicant registration. Only these fields are accepted, so staff
    flags and roles can never be set here."""

    first_name = forms.CharField(max_length=150)
    middle_name = forms.CharField(max_length=150, required=False,
                                  help_text='Leave blank if you have no middle name.')
    last_name = forms.CharField(max_length=150)
    email = forms.EmailField(max_length=254)
    privacy_acknowledged = forms.BooleanField(
        label='I have read and acknowledge the privacy notice.',
        error_messages={'required': 'You must acknowledge the privacy notice to register.'})

    class Meta(auth_forms.BaseUserCreationForm.Meta):
        model = User
        fields = ('username', 'email', 'first_name', 'middle_name', 'last_name')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        return email

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('This username is already taken.')
        return username


class ResendVerificationForm(BootstrapFormMixin, forms.Form):
    email = forms.EmailField(label='Registered email address')


class PrivacyAcknowledgmentForm(BootstrapFormMixin, forms.Form):
    acknowledged = forms.BooleanField(
        label='I have read and acknowledge this privacy notice.',
        error_messages={'required': 'Please confirm that you acknowledge the notice.'})


class PasswordResetForm(BootstrapFormMixin, auth_forms.PasswordResetForm):
    """Each site resets only its own accounts, and the emailed link always
    points to the site the account belongs to."""

    def get_users(self, email):
        return (u for u in super().get_users(email) if portal.account_allowed_here(u))

    def send_mail(self, subject_template_name, email_template_name, context, from_email,
                  to_email, html_email_template_name=None):
        context = {**context}
        context['protocol'], context['domain'] = portal.site_parts_for(context['user'])
        super().send_mail(subject_template_name, email_template_name, context, from_email,
                          to_email, html_email_template_name)


class SetPasswordForm(BootstrapFormMixin, auth_forms.SetPasswordForm):
    pass


class PasswordChangeForm(BootstrapFormMixin, auth_forms.PasswordChangeForm):
    pass


STAFF_ROLE_CHOICES = [(r, r) for r in roles.STAFF_ROLES]


class StaffAccountForm(BootstrapFormMixin, forms.Form):
    username = forms.CharField(max_length=150, validators=[User.username_validator])
    email = forms.EmailField(max_length=254, help_text='The set-password link is sent here.')
    first_name = forms.CharField(max_length=150)
    middle_name = forms.CharField(max_length=150, required=False)
    last_name = forms.CharField(max_length=150)
    roles = forms.MultipleChoiceField(choices=STAFF_ROLE_CHOICES,
                                      widget=forms.CheckboxSelectMultiple)
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}),
                             help_text='Recorded in the audit trail.')

    def clean_email(self):
        email = self.cleaned_data['email'].strip().lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('An account with this email address already exists.')
        return email

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username__iexact=username).exists():
            raise forms.ValidationError('This username is already taken.')
        return username


class UserAccessForm(BootstrapFormMixin, forms.Form):
    roles = forms.MultipleChoiceField(
        choices=STAFF_ROLE_CHOICES, required=False, widget=forms.CheckboxSelectMultiple)
    is_active = forms.BooleanField(label='Account active', required=False,
                                   help_text='Disabled accounts are signed out immediately.')
    reason = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}),
                             help_text='Recorded in the audit trail.')

    def __init__(self, *args, target=None, **kwargs):
        super().__init__(*args, **kwargs)
        if target is not None and roles.has_role(target, roles.APPLICANT):
            self.fields['roles'].choices = [(roles.APPLICANT, roles.APPLICANT)]
            self.fields['roles'].help_text = ('Applicant accounts cannot receive staff roles. '
                                              'Staff need a separate account.')


class UserSearchForm(forms.Form):
    q = forms.CharField(required=False, label='Search')
