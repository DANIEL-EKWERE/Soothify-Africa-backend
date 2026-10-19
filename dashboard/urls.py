"""The staff dashboard, at /dashboard/.

Outside i18n_patterns, like the API and the admin: a tool for the team does
not need a language prefix.
"""

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = 'dashboard'

urlpatterns = [
    path('', views.overview, name='overview'),

    path('waitlist/', views.waitlist, name='waitlist'),
    path('waitlist/export/', views.waitlist_csv, name='waitlist-csv'),

    path('media/', views.media_list, name='media'),
    path('media/new/', views.media_new, name='media-new'),
    path('media/<int:pk>/', views.media_edit, name='media-edit'),
    path('media/<int:pk>/publish/', views.media_publish, name='media-publish'),

    path('applications/', views.applications, name='applications'),
    path('applications/<int:pk>/', views.application_detail, name='application-detail'),
    path('applications/<int:pk>/decide/', views.application_decide,
         name='application-decide'),

    path('messages/', views.messages_list, name='messages'),
    path('messages/new/', views.message_new, name='message-new'),
    path('messages/<int:pk>/', views.message_detail, name='message-detail'),
    path('messages/<int:pk>/send/', views.message_send, name='message-send'),

    path('sessions/', views.sessions, name='sessions'),
    path('recordings/', views.recordings, name='recordings'),
    path('people/', views.people, name='people'),

    path('login/', auth_views.LoginView.as_view(
        template_name='dashboard/login.html', redirect_authenticated_user=True,
    ), name='login'),
    path('logout/', auth_views.LogoutView.as_view(next_page='/dashboard/login/'), name='logout'),
]
