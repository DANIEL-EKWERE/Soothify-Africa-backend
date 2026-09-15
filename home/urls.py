from django.urls import path

from . import views

app_name = 'home'

urlpatterns = [
    path('', views.landing, name='landing'),
    path('privacy-policy/', views.privacy, name='privacy'),
    path('terms-of-use/', views.terms, name='terms'),
]
