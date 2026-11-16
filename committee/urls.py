from django.urls import path

from . import views

app_name = 'committee'

urlpatterns = [
    # Committees and conflicts
    path('vacancy/<uuid:pk>/', views.vacancy_committees, name='vacancy_committees'),
    path('<uuid:committee_pk>/members/add/', views.member_add, name='member_add'),
    path('<uuid:pk>/active/', views.committee_active, name='committee_active'),
    path('members/<uuid:pk>/end/', views.member_end, name='member_end'),
    path('conflicts/<uuid:pk>/resolve/', views.conflict_resolve, name='conflict_resolve'),
    # Interviews (HR)
    path('applications/<uuid:app_pk>/interviews/new/', views.interview_new,
         name='interview_new'),
    path('interviews/<uuid:pk>/reschedule/', views.interview_reschedule,
         name='interview_reschedule'),
    path('interviews/<uuid:pk>/cancel/', views.interview_cancel, name='interview_cancel'),
    path('interviews/<uuid:pk>/attendance/', views.interview_attendance,
         name='interview_attendance'),
    path('interviews/<uuid:pk>/panel/add/', views.panel_add, name='panel_add'),
    path('interviews/<uuid:pk>/panel/<uuid:member_pk>/remove/', views.panel_remove,
         name='panel_remove'),
    # Committee members
    path('candidates/', views.my_candidates, name='my_candidates'),
    path('candidates/<uuid:pk>/', views.candidate, name='candidate'),
    path('candidates/<uuid:pk>/conflict/', views.conflict_declare, name='conflict_declare'),
    path('candidates/<uuid:pk>/evaluations/new/', views.evaluation_new, name='evaluation_new'),
    path('evaluations/<uuid:pk>/', views.evaluation_edit, name='evaluation_edit'),
    path('evaluations/<uuid:pk>/discard/', views.evaluation_discard, name='evaluation_discard'),
    path('evaluations/<uuid:pk>/amend/', views.evaluation_amend, name='evaluation_amend'),
]
