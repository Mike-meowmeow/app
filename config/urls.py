from django.contrib import admin
from django.urls import include, path

from accounts.views import admin_login_redirect

admin.site.site_header = 'EVSU Recruitment - Administration'
admin.site.site_title = 'EVSU Recruitment admin'

urlpatterns = [
    # Before the admin's own URLs: the admin signs in through the staff page.
    path('admin/login/', admin_login_redirect),
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('profile/', include('applicants.urls')),
    path('recruitment/', include('recruitment.urls')),
    path('files/', include('documents.urls')),
    path('rosters/', include('matrix.urls')),
    path('committee/', include('committee.urls')),
    path('notifications/', include('notifications.urls')),
    path('analytics/', include('analytics.urls')),
    path('audit/', include('audit.urls')),
    path('operations/', include('operations.urls')),
    path('ai/', include('ai_processing.urls')),
    path('assistant/', include('assistant.urls')),
    path('', include('core.urls')),
]
