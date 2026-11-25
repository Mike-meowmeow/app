from django.urls import path

from . import views

app_name = 'accounts'

urlpatterns = [
    path('login/', views.LoginView.as_view(), name='login'),
    path('staff/login/', views.StaffLoginView.as_view(), name='staff_login'),
    path('staff/login/code/', views.two_factor_verify, name='two_factor_verify'),
    path('staff/two-factor/setup/', views.two_factor_setup, name='two_factor_setup'),
    path('staff/two-factor/recovery-codes/', views.two_factor_recovery_codes,
         name='two_factor_recovery_codes'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('register/', views.register, name='register'),
    path('verify/resend/', views.resend_verification, name='resend_verification'),
    path('verify/<str:token>/', views.verify_email, name='verify_email'),
    path('privacy/', views.privacy, name='privacy'),
    path('account/', views.account, name='account'),
    path('password/change/', views.PasswordChangeView.as_view(), name='password_change'),
    path('password/reset/', views.PasswordResetView.as_view(), name='password_reset'),
    path('password/reset/sent/', views.PasswordResetDoneView.as_view(),
         name='password_reset_done'),
    path('password/reset/<uidb64>/<token>/', views.PasswordResetConfirmView.as_view(),
         name='password_reset_confirm'),
    path('password/reset/complete/', views.PasswordResetCompleteView.as_view(),
         name='password_reset_complete'),
    path('admin/users/', views.user_list, name='user_list'),
    path('admin/users/new/', views.staff_create, name='staff_create'),
    path('admin/users/<uuid:pk>/', views.user_access, name='user_access'),
    path('admin/users/<uuid:pk>/invite/', views.staff_invite, name='staff_invite'),
    path('admin/users/<uuid:pk>/two-factor/reset/', views.two_factor_reset,
         name='two_factor_reset'),
]
