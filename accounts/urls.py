"""Auth endpoints, mounted by api/urls.py under /api/v1/auth/."""

from django.urls import path

from . import views

app_name = 'accounts'

urlpatterns = [
    path('register/', views.RegisterView.as_view(), name='register'),
    path('login/', views.LoginView.as_view(), name='login'),
    # Social sign-in. One view, told which provider it is serving.
    path(
        'google/',
        views.SocialLoginView.as_view(provider='google'),
        name='google',
    ),
    path(
        'apple/',
        views.SocialLoginView.as_view(provider='apple'),
        name='apple',
    ),
    path('logout/', views.LogoutView.as_view(), name='logout'),
    path('email-available/', views.EmailAvailableView.as_view(), name='email-available'),

    path('password/reset/', views.PasswordResetRequestView.as_view(), name='password-reset'),
    path('password/reset/confirm/', views.PasswordResetConfirmView.as_view(),
         name='password-reset-confirm'),

    path('me/', views.MeView.as_view(), name='me'),
    # Closing an account and erasing what is personal. A POST, because
    # the one irreversible action here should not be a verb away from
    # an ordinary profile edit.
    path('me/delete/', views.DeleteAccountView.as_view(), name='delete-account'),

    path('kyc/', views.KycView.as_view(), name='kyc'),
    path('kyc/complete/', views.KycCompleteView.as_view(), name='kyc-complete'),
]
