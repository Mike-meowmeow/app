from django.urls import path

from . import views

app_name = 'recruitment'

urlpatterns = [
    # Public / applicants
    path('vacancies/', views.public_list, name='public_list'),
    path('vacancies/<uuid:pk>/', views.public_detail, name='public_detail'),
    # Staff
    path('manage/vacancies/', views.vacancy_list, name='vacancy_list'),
    path('manage/vacancies/new/', views.vacancy_create, name='vacancy_create'),
    path('manage/vacancies/<uuid:pk>/', views.vacancy_detail, name='vacancy_detail'),
    path('manage/vacancies/<uuid:pk>/edit/', views.vacancy_edit, name='vacancy_edit'),
    path('manage/vacancies/<uuid:pk>/versions/<int:version>/', views.version_detail,
         name='version_detail'),
    path('manage/vacancies/<uuid:pk>/requirements/start/', views.requirements_start,
         name='requirements_start'),
    path('manage/vacancies/<uuid:pk>/requirements/discard/', views.requirements_discard,
         name='requirements_discard'),
    path('manage/vacancies/<uuid:pk>/requirements/<slug:kind>/add/', views.requirement_add,
         name='requirement_add'),
    path('manage/vacancies/<uuid:pk>/requirements/<slug:kind>/<uuid:item_pk>/remove/',
         views.requirement_remove, name='requirement_remove'),
    path('manage/vacancies/<uuid:pk>/publish/', views.vacancy_publish, name='vacancy_publish'),
    path('manage/vacancies/<uuid:pk>/close/', views.vacancy_close, name='vacancy_close'),
    path('manage/vacancies/<uuid:pk>/cancel/', views.vacancy_cancel, name='vacancy_cancel'),
    path('manage/vacancies/<uuid:pk>/delete/', views.vacancy_delete, name='vacancy_delete'),
    path('manage/vacancies/<uuid:pk>/assignments/', views.assignment_add, name='assignment_add'),
    path('manage/vacancies/<uuid:pk>/assignments/<uuid:assignment_pk>/end/',
         views.assignment_end, name='assignment_end'),
]
