"""Audit trail search and export for authorized auditors (FR-017).

HR Head / Approver and System Administrator may search; the trail itself
cannot be edited or deleted here. Exports are CSV, limited in size, recorded
in the audit trail, and protected against spreadsheet formula injection.
"""

import csv
from datetime import datetime, time, timedelta

from django import forms
from django.core.paginator import Paginator
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone

from accounts import roles
from accounts.access import role_required
from core.forms import BootstrapFormMixin

from . import services
from .models import AuditLog

AUDITOR_ROLES = (roles.APPROVER, roles.SYSTEM_ADMIN)
EXPORT_LIMIT = 10000


class AuditFilterForm(BootstrapFormMixin, forms.Form):
    action = forms.CharField(required=False, label='Action starts with',
                             widget=forms.TextInput(attrs={'placeholder': 'e.g. roster. or auth.'}))
    actor = forms.CharField(required=False, label='User')
    entity_id = forms.CharField(required=False, label='Record ID')
    start = forms.DateField(required=False, label='From',
                            widget=forms.DateInput(attrs={'type': 'date'}))
    end = forms.DateField(required=False, label='To (inclusive)',
                          widget=forms.DateInput(attrs={'type': 'date'}))


def _filtered(data):
    qs = AuditLog.objects.select_related('actor').order_by('-occurred_at')
    if data.get('action'):
        qs = qs.filter(action__startswith=data['action'].strip())
    if data.get('actor'):
        qs = qs.filter(actor__username__iexact=data['actor'].strip())
    if data.get('entity_id'):
        qs = qs.filter(entity_id__startswith=data['entity_id'].strip().lower())
    tz = timezone.get_current_timezone()
    if data.get('start'):
        qs = qs.filter(occurred_at__gte=timezone.make_aware(
            datetime.combine(data['start'], time.min), tz))
    if data.get('end'):
        qs = qs.filter(occurred_at__lt=timezone.make_aware(
            datetime.combine(data['end'] + timedelta(days=1), time.min), tz))
    return qs


def _cell(value):
    text = '' if value is None else str(value)
    return "'" + text if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


@role_required(*AUDITOR_ROLES)
def audit_list(request):
    form = AuditFilterForm(request.GET or None)
    data = form.cleaned_data if form.is_valid() else {}
    qs = _filtered(data)
    if request.GET.get('export') == 'csv':
        rows = list(qs[:EXPORT_LIMIT])
        services.record('audit.exported', actor=request.user, entity_type='audit.AuditLog',
                        changes={'filters': {k: str(v) for k, v in data.items() if v},
                                 'rows': len(rows)})
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="audit-trail.csv"'
        response['Cache-Control'] = 'private, no-store'
        response.write('﻿')  # Excel opens UTF-8 correctly with a BOM
        writer = csv.writer(response)
        writer.writerow(['Occurred (Asia/Manila)', 'Action', 'User', 'Record type', 'Record ID',
                         'Reason', 'Changes', 'IP address'])
        for log in rows:
            writer.writerow([_cell(timezone.localtime(log.occurred_at).strftime('%Y-%m-%d %H:%M:%S')),
                             _cell(log.action), _cell(log.actor.username if log.actor else ''),
                             _cell(log.entity_type), _cell(log.entity_id), _cell(log.reason),
                             _cell(log.changes), _cell(log.ip_address)])
        return response
    page = Paginator(qs, 50).get_page(request.GET.get('page'))
    query = request.GET.copy()
    query.pop('page', None)
    return render(request, 'audit/list.html', {'form': form, 'page': page,
                                               'query': query.urlencode(),
                                               'export_limit': EXPORT_LIMIT})
