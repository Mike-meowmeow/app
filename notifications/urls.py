from django.urls import path

from . import views

app_name = 'notifications'

urlpatterns = [
    path('', views.notification_list, name='list'),
    path('<uuid:pk>/open/', views.notification_open, name='open'),
    path('read-all/', views.mark_all_read, name='mark_all_read'),
    path('deliveries/', views.deliveries, name='deliveries'),
    path('deliveries/retry/', views.retry, name='retry'),
]
