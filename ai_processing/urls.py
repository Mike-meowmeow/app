from django.urls import path

from . import views

app_name = 'ai_processing'

APP = 'applications/<uuid:pk>/'

urlpatterns = [
    path('extraction-results/', views.results, name='results'),
    path(APP + 'documents/<uuid:version_pk>/extract/', views.run, name='run'),
    path(APP + 'jobs/<uuid:job_pk>/', views.extraction, name='extraction'),
    path(APP + 'jobs/<uuid:job_pk>/retry/', views.retry, name='retry'),
    path(APP + 'items/<uuid:item_pk>/', views.item_action, name='item_action'),
    path(APP + 'items/<uuid:item_pk>/edit/', views.item_edit, name='item_edit'),
    path(APP + 'checks/run/', views.run_checks, name='run_checks'),
    path(APP + 'checks/<uuid:screening_pk>/', views.screening_detail, name='screening'),
]
