"""Auth endpoints, mounted by api/urls.py under /api/v1/auth/."""

from django.urls import path

from . import views

app_name = 'accounts'

urlpatterns = [
    path('register/', views.RegisterView.as_view(), name='register'),
    path('login/', views.LoginView.as_view(), name='login'),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('email-available/', views.EmailAvailableView.as_view(), name='email-available'),

    path('me/', views.MeView.as_view(), name='me'),

    path('kyc/', views.KycView.as_view(), name='kyc'),
    path('kyc/complete/', views.KycCompleteView.as_view(), name='kyc-complete'),
]
