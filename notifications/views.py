from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from accounts import roles
from accounts.access import role_required

from . import services
from .models import DeliveryStatus, Notification, NotificationDelivery


@login_required
def notification_list(request):
    qs = Notification.objects.filter(user=request.user)
    if request.GET.get('show') == 'unread':
        qs = qs.filter(read_at__isnull=True)
    page = Paginator(qs, 25).get_page(request.GET.get('page'))
    return render(request, 'notifications/list.html',
                  {'page': page, 'show': request.GET.get('show', '')})


@login_required
def notification_open(request, pk):
    """Mark as read and go to the related record the user may open."""
    notification = get_object_or_404(Notification, pk=pk, user=request.user)
    services.mark_read(request.user, notification)
    target = notification.target_url()
    if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        target = 'notifications:list'
    return redirect(target)


@login_required
@require_POST
def mark_all_read(request):
    count = services.mark_all_read(request.user)
    messages.success(request, f'{count} notification(s) marked as read.')
    return redirect('notifications:list')


@role_required(roles.SYSTEM_ADMIN)
def deliveries(request):
    """Delivery monitoring and retry for administrators. Bodies are not shown."""
    failed = NotificationDelivery.objects.filter(status=DeliveryStatus.FAILED) \
        .select_related('notification__user').order_by('-attempted_at')[:100]
    return render(request, 'notifications/deliveries.html', {
        'failed': failed, 'pending': services.pending_email().count(),
        'max_attempts': settings.NOTIFICATION_MAX_ATTEMPTS})


@role_required(roles.SYSTEM_ADMIN)
@require_POST
def retry(request):
    sent, failed = services.retry_pending(request.user)
    messages.info(request, f'Retry finished: {sent} sent, {failed} failed.')
    return redirect('notifications:deliveries')
