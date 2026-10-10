"""Spaces endpoints, under /api/v1/spaces/."""

from django.urls import path

from . import views

app_name = 'spaces'

urlpatterns = [
    # ?category=&q=&lat=&lng=
    path('', views.SpaceListView.as_view(), name='list'),
    path('passport-waitlist/', views.PassportWaitlistView.as_view(), name='passport-waitlist'),
    path('<slug:pk>/', views.SpaceDetailView.as_view(), name='detail'),
]
