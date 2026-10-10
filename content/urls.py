"""Content endpoints, mounted by api/urls.py under /api/v1/content/."""

from django.urls import path

from . import views

app_name = 'content'

urlpatterns = [
    path('categories/', views.CategoryListView.as_view(), name='categories'),

    # ?q= search, ?category=, ?type=, ?free=true
    path('items/', views.MediaListView.as_view(), name='items'),
    path('items/<int:pk>/', views.MediaDetailView.as_view(), name='item'),
    path('items/<int:pk>/plays/', views.record_playback, name='item-plays'),
    path('items/<int:pk>/rating/', views.rate_item, name='item-rating'),

    path('featured/', views.FeaturedView.as_view(), name='featured'),
    path('recommendations/', views.RecommendationsView.as_view(), name='recommendations'),
    path('recent/', views.RecentView.as_view(), name='recent'),

    # ?full=true for the page behind "See All"
    path('shelves/<slug:key>/', views.ShelfView.as_view(), name='shelf'),
]
