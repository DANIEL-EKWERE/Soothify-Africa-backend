"""Mood, journal and stats, under /api/v1/wellbeing/."""

from django.urls import path

from . import views

app_name = 'wellbeing'

urlpatterns = [
    # ?from=&to=&limit= for the week strip and the history list
    path('moods/', views.MoodListView.as_view(), name='moods'),
    path('moods/today/', views.MoodTodayView.as_view(), name='mood-today'),

    path('journal/', views.JournalListView.as_view(), name='journal'),
    path('journal/<int:pk>/', views.JournalDetailView.as_view(), name='journal-entry'),

    path('stats/', views.ProfileStatsView.as_view(), name='stats'),
]
