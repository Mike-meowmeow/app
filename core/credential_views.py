"""The license and eligibility reference list: a JSON endpoint for signed-in
users, and the System Administrator's page for keeping the list up to date."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_http_methods

from accounts import roles
from accounts.access import role_required
from audit import services as audit

from . import credentials
from .forms import BootstrapFormMixin
from .models import Credential, CredentialKind


@login_required
@require_GET
def credential_names(request):
    """GET ?kind=license|eligibility&q=words -> {"kind", "results": [{"name", "aliases"}]}."""
    kind = request.GET.get('kind', '')
    if kind not in CredentialKind.values:
        return JsonResponse({'error': 'kind must be "license" or "eligibility".'}, status=400)
    query = request.GET.get('q', '')[:100]
    return JsonResponse({'kind': kind, 'results': credentials.search(kind, query)})


class CredentialForm(BootstrapFormMixin, forms.ModelForm):
    class Meta:
        model = Credential
        fields = ('kind', 'name', 'aliases', 'is_active')
        labels = {'is_active': 'Suggest this name (uncheck to retire it)'}
        widgets = {'aliases': forms.Textarea(attrs={'rows': 3})}

    def clean_name(self):
        return ' '.join(self.cleaned_data['name'].split())


def _save(request, form, action):
    try:
        with transaction.atomic():
            credential = form.save()
    except IntegrityError:
        form.add_error('name', 'This name is already in the list.')
        return None
    audit.record(action, entity=credential, actor=request.user,
                 changes={'kind': credential.kind, 'name': credential.name,
                          'aliases': credential.alias_list(), 'active': credential.is_active})
    return credential


@role_required(roles.SYSTEM_ADMIN)
@require_http_methods(['GET', 'POST'])
def credential_list(request):
    form = CredentialForm(request.POST or None, initial={'is_active': True})
    if request.method == 'POST' and form.is_valid():
        credential = _save(request, form, 'credential.created')
        if credential:
            messages.success(request, f'"{credential.name}" added.')
            return redirect('core:credential_list')
    kind = request.GET.get('kind')
    shown = Credential.objects.all()
    if kind in CredentialKind.values:
        shown = shown.filter(kind=kind)
    return render(request, 'core/credentials.html', {
        'form': form, 'credentials': shown, 'kind': kind, 'kinds': CredentialKind.choices})


@role_required(roles.SYSTEM_ADMIN)
@require_http_methods(['GET', 'POST'])
def credential_edit(request, pk):
    credential = get_object_or_404(Credential, pk=pk)
    form = CredentialForm(request.POST or None, instance=credential)
    if request.method == 'POST' and form.is_valid():
        if _save(request, form, 'credential.updated'):
            messages.success(request, f'"{credential.name}" saved.')
            return redirect('core:credential_list')
    return render(request, 'core/credential_edit.html', {'form': form, 'credential': credential})
