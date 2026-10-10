"""The one public endpoint: ZEGOCLOUD's upload callback."""

from django.urls import path

from . import views

app_name = 'recordings'

urlpatterns = [
    path('callback/', views.recording_ready, name='callback'),
]
