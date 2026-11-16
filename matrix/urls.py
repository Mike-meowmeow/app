from django.urls import path

from . import views

app_name = 'matrix'

R = '<uuid:pk>/'

urlpatterns = [
    path('vacancy/<uuid:pk>/', views.vacancy_rosters, name='vacancy_rosters'),
    path('pending/', views.pending, name='pending'),
    path('templates/', views.templates, name='templates'),
    path('templates/<uuid:pk>/signatories/', views.template_signatories,
         name='template_signatories'),
    path('templates/<uuid:pk>/approve/', views.template_approve, name='template_approve'),
    path('exports/<uuid:pk>/download/', views.download, name='download'),
    path(R, views.roster_detail, name='roster_detail'),
    path(R + 'regenerate/', views.regenerate, name='regenerate'),
    path(R + 'new-version/', views.new_version, name='new_version'),
    path(R + 'entries/<uuid:entry_pk>/', views.entry_edit, name='entry_edit'),
    path(R + 'signatories/', views.signatories, name='signatories'),
    path(R + 'submit/', views.submit_for_approval, name='submit_for_approval'),
    path(R + 'approve/', views.approve, name='approve'),
    path(R + 'return/', views.return_roster, name='return'),
    path(R + 'release/', views.release, name='release'),
    path(R + 'export/<str:fmt>/', views.export_draft, name='export_draft'),
]
