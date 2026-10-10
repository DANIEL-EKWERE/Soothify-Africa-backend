"""Corporate endpoints, under /api/v1/corporate/."""

from django.urls import path

from . import views

app_name = 'corporate'

urlpatterns = [
    path('enquiries/', views.EnquiryCreateView.as_view(), name='enquiries'),
]
