"""Billing endpoints, under /api/v1/billing/."""

from django.urls import path

from . import views

app_name = 'billing'

urlpatterns = [
    # ?period=monthly|annual
    path('plans/', views.PlanListView.as_view(), name='plans'),

    path('membership/', views.MembershipView.as_view(), name='membership'),
    path('membership/cancel/', views.cancel_membership, name='membership-cancel'),
    # Changing the card on file. The same small check as the free week.
    path('card/', views.ReplaceCardView.as_view(), name='card'),
    path('invoices/', views.InvoiceListView.as_view(), name='invoices'),

    path('checkout/', views.CheckoutView.as_view(), name='checkout'),
    path('sessions/<int:pk>/checkout/', views.SessionCheckoutView.as_view(),
         name='session-checkout'),
    path('promo/', views.check_promo, name='promo'),
    path('payments/<str:reference>/', views.payment_status, name='payment'),

    # Paystack calls this; it is the only thing that marks anything paid.
    path('webhook/', views.webhook, name='webhook'),
]
