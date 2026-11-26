from django.urls import path

from . import credential_views, views

app_name = 'core'

urlpatterns = [
    path('', views.home, name='home'),
    path('dashboard/', views.dashboard, name='dashboard'),
    path('workspace/applicant/', views.workspace_applicant, name='workspace_applicant'),
    path('workspace/hr/', views.workspace_hr, name='workspace_hr'),
    path('workspace/approver/', views.workspace_approver, name='workspace_approver'),
    path('workspace/department/', views.workspace_department, name='workspace_department'),
    path('workspace/psb/', views.workspace_psb, name='workspace_psb'),
    path('reference/credential-names/', credential_views.credential_names,
         name='credential_names'),
    path('reference/credentials/', credential_views.credential_list, name='credential_list'),
    path('reference/credentials/<uuid:pk>/', credential_views.credential_edit,
         name='credential_edit'),
]
