from django.urls import path

from . import application_views as apps
from . import decision_views, position_views
from . import views

app_name = 'recruitment'

APP = 'manage/applications/<uuid:pk>/'

urlpatterns = [
    # Applicant: applications
    path('vacancies/<uuid:vacancy_pk>/apply/', apps.apply, name='apply'),
    path('my-applications/', apps.my_applications, name='my_applications'),
    path('my-applications/<uuid:pk>/', apps.my_application, name='my_application'),
    path('my-applications/<uuid:pk>/upload/', apps.upload_document, name='upload_document'),
    path('my-applications/<uuid:pk>/documents/<uuid:document_pk>/remove/', apps.remove_document,
         name='remove_document'),
    path('my-applications/<uuid:pk>/submit/', apps.submit_application, name='submit_application'),
    path('my-applications/<uuid:pk>/profile-files/', apps.import_profile_documents,
         name='import_profile_documents'),
    path('my-applications/<uuid:pk>/resubmit/', apps.resubmit_corrections,
         name='resubmit_corrections'),
    path('my-applications/<uuid:pk>/withdraw/', apps.withdraw, name='withdraw'),
    # Staff: applications
    path('manage/vacancies/<uuid:pk>/applications/', apps.vacancy_applications,
         name='vacancy_applications'),
    path('manage/verification-queue/', apps.verification_queue, name='verification_queue'),
    path('manage/applications/', apps.application_search, name='application_search'),
    path(APP, apps.review, name='application_review'),
    path(APP + 'start-review/', apps.start_review, name='application_start_review'),
    path(APP + 'reopen/', apps.reopen, name='application_reopen'),
    path(APP + 'documents/<uuid:version_pk>/verify/', apps.verify_document,
         name='verify_document'),
    path(APP + 'corrections/new/', apps.request_correction, name='request_correction'),
    path(APP + 'corrections/<uuid:request_pk>/exception/', apps.correction_exception,
         name='correction_exception'),
    path(APP + 'facts/add/', apps.fact_add, name='fact_add'),
    path(APP + 'facts/<uuid:fact_pk>/review/', apps.fact_review, name='fact_review'),
    path(APP + 'facts/<uuid:fact_pk>/evidence/', apps.fact_evidence, name='fact_evidence'),
    path(APP + 'facts/<uuid:fact_pk>/evidence/<uuid:link_pk>/unlink/', apps.fact_unlink,
         name='fact_unlink'),
    path(APP + 'facts/<uuid:fact_pk>/correct/', apps.fact_correct, name='fact_correct'),
    path(APP + 'qualifications/<uuid:standard_pk>/', apps.qualification_outcome,
         name='qualification_outcome'),
    # Staff: selection and decisions (Stage 7)
    path(APP + 'selection/', decision_views.application_selection,
         name='application_selection'),
    path(APP + 'decisions/<slug:decision_type>/new/', decision_views.decision_new,
         name='decision_new'),
    path(APP + 'decisions/<uuid:decision_pk>/correct/', decision_views.decision_correct,
         name='decision_correct'),
    path(APP + 'decisions/<uuid:decision_pk>/release/', decision_views.decision_release,
         name='decision_release'),
    path('manage/decisions/pending-release/', decision_views.decisions_pending,
         name='decisions_pending'),

    # Public / applicants
    path('vacancies/', views.public_list, name='public_list'),
    path('vacancies/<uuid:pk>/', views.public_detail, name='public_detail'),
    # Staff
    path('manage/vacancies/', views.vacancy_list, name='vacancy_list'),
    path('manage/vacancies/new/', views.vacancy_create, name='vacancy_create'),
    path('manage/vacancies/<uuid:pk>/', views.vacancy_detail, name='vacancy_detail'),
    path('manage/vacancies/<uuid:pk>/edit/', views.vacancy_edit, name='vacancy_edit'),
    path('manage/vacancies/<uuid:pk>/load-position/', views.vacancy_apply_position,
         name='vacancy_apply_position'),
    # Staff: position catalog
    path('manage/positions/', position_views.position_list, name='position_list'),
    path('manage/positions/new/', position_views.position_create, name='position_create'),
    path('manage/positions/<uuid:pk>/', position_views.position_detail, name='position_detail'),
    path('manage/positions/<uuid:pk>/edit/', position_views.position_edit, name='position_edit'),
    path('manage/positions/<uuid:pk>/<slug:action>/', position_views.position_set_active,
         name='position_set_active'),
    path('manage/positions/<uuid:pk>/defaults/<slug:kind>/add/', position_views.position_item_add,
         name='position_item_add'),
    path('manage/positions/<uuid:pk>/defaults/<slug:kind>/<uuid:item_pk>/remove/',
         position_views.position_item_remove, name='position_item_remove'),
    path('manage/vacancies/<uuid:pk>/versions/<int:version>/', views.version_detail,
         name='version_detail'),
    path('manage/vacancies/<uuid:pk>/requirements/start/', views.requirements_start,
         name='requirements_start'),
    path('manage/vacancies/<uuid:pk>/requirements/discard/', views.requirements_discard,
         name='requirements_discard'),
    path('manage/vacancies/<uuid:pk>/requirements/<slug:kind>/add/', views.requirement_add,
         name='requirement_add'),
    path('manage/vacancies/<uuid:pk>/requirements/standard/<uuid:item_pk>/keep/',
         views.requirement_keep, name='requirement_keep'),
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
