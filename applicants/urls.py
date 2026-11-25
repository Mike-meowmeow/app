from django.urls import path

from . import views

app_name = 'applicants'

urlpatterns = [
    path('', views.profile, name='profile'),
    path('personal/', views.edit_personal, name='edit_personal'),
    # Before '<kind>/<pk>/', which would otherwise match 'files/<pk>/'.
    path('files/<uuid:pk>/', views.document_file, name='document_file'),
    path('files/<uuid:pk>/remove/', views.remove_document, name='remove_document'),
    path('<slug:kind>/add/', views.edit_record, name='add_record'),
    path('<slug:kind>/<uuid:pk>/', views.edit_record, name='edit_record'),
    path('<slug:kind>/<uuid:pk>/delete/', views.delete_record, name='delete_record'),
    path('<slug:kind>/<uuid:pk>/files/', views.attach_document, name='attach_document'),
]
