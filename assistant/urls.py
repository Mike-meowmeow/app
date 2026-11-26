from django.urls import path

from . import views

app_name = 'assistant'

urlpatterns = [
    path('ask/', views.ask, name='ask'),
    path('run/', views.run, name='run'),
    path('feedback/', views.feedback, name='feedback'),
    path('unanswered/', views.unanswered, name='unanswered'),
]
