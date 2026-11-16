from django.contrib import admin
from django.urls import include, path

admin.site.site_header = 'EVSU Recruitment - Administration'
admin.site.site_title = 'EVSU Recruitment admin'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('accounts/', include('accounts.urls')),
    path('', include('core.urls')),
]
