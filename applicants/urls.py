from django.urls import path

from . import views

app_name = 'applicants'

urlpatterns = [
    path('', views.profile, name='profile'),
    path('personal/', views.edit_personal, name='edit_personal'),
    path('<slug:kind>/add/', views.edit_record, name='add_record'),
    path('<slug:kind>/<uuid:pk>/', views.edit_record, name='edit_record'),
    path('<slug:kind>/<uuid:pk>/delete/', views.delete_record, name='delete_record'),
]
