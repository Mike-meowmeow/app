from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from accounts.access import role_required
from core.files import private_file_response

from . import metrics, services
from .forms import ReportForm, ScopeForm
from .models import AnalyticsReport

ROLES = services.ANALYTICS_ROLES


def _scope(request):
    """(form, vacancies, scope, start, end, vacancy) from the query string."""
    permitted = services.scope_vacancies(request.user)
    form = ScopeForm(request.GET or None, vacancies=permitted)
    data = form.cleaned_data if form.is_valid() else {}
    department, vacancy = data.get('department'), data.get('vacancy')
    start, end = services.period_bounds(data.get('start'), data.get('end'))
    vacancies = services.scope_vacancies(request.user, department=department, vacancy=vacancy)
    scope = services.describe_scope(request.user, vacancies, department=department,
                                    vacancy=vacancy, start=start, end=end)
    return form, vacancies, scope, start, end, vacancy


@role_required(*ROLES)
@require_http_methods(['GET', 'POST'])
def dashboard(request):
    form, vacancies, scope, start, end, vacancy = _scope(request)
    report_form = ReportForm(request.POST or None)
    query = request.GET.urlencode()
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'snapshot':
            services.save_snapshot(request.user, vacancies, scope, vacancy=vacancy, start=start,
                                   end=end)
            messages.success(request, 'Metric snapshot saved.')
            return redirect('analytics:snapshots')
        if action == 'report' and report_form.is_valid():
            try:
                report = services.generate_report(
                    request.user, report_form.cleaned_data['report_type'],
                    report_form.cleaned_data['fmt'], vacancies, scope, start=start, end=end)
            except (services.ReportFailed, ValidationError) as exc:
                messages.error(request, str(exc.messages[0] if hasattr(exc, 'messages')
                                            else exc))
            else:
                messages.success(request, f'{report} generated.')
                return redirect('analytics:reports')
        return redirect(f"{request.path}?{query}")
    values = metrics.compute(vacancies, period_start=start, period_end=end)
    fills = metrics.time_to_fill(vacancies)
    per_vacancy = []
    for v in vacancies.select_related('department').order_by('position_title')[:50]:
        m = metrics.compute(vacancies.filter(pk=v.pk), period_start=start, period_end=end)
        per_vacancy.append({'vacancy': v, 'm': m,
                            'time_to_fill': metrics.format_duration(fills.get(v.pk))})
    return render(request, 'analytics/dashboard.html', {
        'form': form, 'report_form': ReportForm(), 'values': values,
        'scope_label': services.scope_label(scope), 'query': query,
        'rates': {
            'shortlisting': metrics.format_rate(values['shortlisting_rate']),
            'interviews': metrics.format_rate(values['interview_completion_rate']),
            'recommendation': metrics.format_rate(values['recommendation_rate']),
        },
        'time_to_fill': metrics.format_duration(values['time_to_fill_seconds']),
        'per_vacancy': per_vacancy, 'definitions': metrics.DEFINITIONS,
        'max_trend': max([n for _, n in values['trend']] or [1]),
    })


@role_required(*ROLES)
def snapshots(request):
    rows = [(s, services.scope_label(s.scope), metrics.format_duration(s.time_to_fill_seconds))
            for s in services.visible_snapshots(request.user)]
    return render(request, 'analytics/snapshots.html', {'rows': rows})


@role_required(*ROLES)
def reports(request):
    rows = [(r, services.scope_label(r.scope)) for r in services.visible_reports(request.user)]
    return render(request, 'analytics/reports.html', {'rows': rows})


@role_required(*ROLES)
def report_download(request, pk):
    report = get_object_or_404(AnalyticsReport, pk=pk)
    if not services.can_download(request.user, report):
        raise Http404
    path = services.report_path(report)
    if not path.exists():
        raise Http404
    services.record_download(request.user, report)
    return private_file_response(path, filename=report.file_name)
