"""Expert and session endpoints, under /api/v1/."""

from django.urls import path

from . import views

app_name = 'experts'

urlpatterns = [
    # Browsing and booking, from the client's side
    path('experts/', views.ExpertListView.as_view(), name='list'),
    path('experts/match/', views.match, name='match'),
    path('experts/applications/', views.ApplicationView.as_view(), name='applications'),

    # The practitioner's own screens
    path('experts/me/availability/', views.MyAvailabilityView.as_view(), name='my-availability'),
    path('experts/me/availability/<int:pk>/', views.MyAvailabilityDetailView.as_view(),
         name='my-availability-slot'),
    path('experts/me/sessions/', views.MySessionsView.as_view(), name='my-sessions'),
    path('experts/me/earnings/', views.MyEarningsView.as_view(), name='my-earnings'),
    path('experts/me/payouts/', views.MyPayoutsView.as_view(), name='my-payouts'),
    path('experts/me/payout-methods/', views.MyPayoutMethodsView.as_view(),
         name='my-payout-methods'),
    # The banks a payout account can be at, asked of Bachs.
    path('experts/me/banks/', views.MyBanksView.as_view(), name='my-banks'),

    path('experts/<int:pk>/', views.ExpertDetailView.as_view(), name='detail'),
    path('experts/<int:pk>/slots/', views.ExpertSlotsView.as_view(), name='slots'),

    # Sessions
    path('sessions/', views.SessionListView.as_view(), name='sessions'),
    path('sessions/<int:pk>/', views.SessionDetailView.as_view(), name='session'),
    path('sessions/<int:pk>/cancel/', views.cancel_session, name='session-cancel'),
    path('sessions/<int:pk>/rating/', views.rate_session, name='session-rating'),
    path('sessions/<int:pk>/call-token/', views.call_token, name='session-call-token'),
    path('sessions/<int:pk>/note/', views.SessionNoteView.as_view(), name='session-note'),
]
