from django.urls import path

from . import views

app_name = 'analytics'

urlpatterns = [
    path('', views.dashboard, name='dashboard'),
    path('snapshots/', views.snapshots, name='snapshots'),
    path('reports/', views.reports, name='reports'),
    path('reports/<uuid:pk>/download/', views.report_download, name='report_download'),
]
