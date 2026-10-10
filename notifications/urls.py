"""Notification endpoints, under /api/v1/notifications/."""

from django.urls import path

from . import views

app_name = 'notifications'

urlpatterns = [
    # ?filter=sessions | whats_new
    path('', views.FeedView.as_view(), name='feed'),
    path('unread/', views.unread_count, name='unread'),
    path('read/', views.mark_read, name='read-all'),
    path('<int:pk>/read/', views.mark_read, name='read'),

    path('preferences/', views.PreferencesView.as_view(), name='preferences'),
    path('devices/', views.PushDeviceView.as_view(), name='devices'),
]
