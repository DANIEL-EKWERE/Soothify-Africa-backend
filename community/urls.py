"""Forum endpoints, under /api/v1/community/."""

from django.urls import path

from . import views

app_name = 'community'

urlpatterns = [
    path('handle-available/', views.handle_available, name='handle-available'),

    path('discussions/', views.DiscussionListView.as_view(), name='discussions'),
    path('discussions/<int:pk>/', views.DiscussionDetailView.as_view(), name='discussion'),
    path('discussions/<int:pk>/comments/', views.CommentListView.as_view(), name='comments'),
    path('discussions/<int:pk>/like/', views.like, name='like'),
    path('discussions/<int:pk>/share/', views.share, name='share'),
    path('discussions/<int:pk>/report/', views.report, name='report'),

    path('comments/<int:pk>/', views.CommentDetailView.as_view(), name='comment'),
]
