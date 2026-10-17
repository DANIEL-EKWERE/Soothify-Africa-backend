from django.urls import path

from . import views

app_name = 'home'

urlpatterns = [
    path('', views.landing, name='landing'),
    path('privacy-policy/', views.privacy, name='privacy'),
    path('terms-of-use/', views.terms, name='terms'),
    path('waitlist/', views.waitlist, name='waitlist'),
    path('waitlist/done/', views.waitlist_done, name='waitlist_done'),

    # Practitioners applying to practise here, without an account. The
    # app's own shape: an intro, three steps, then the acknowledgement.
    path('practitioners/apply/', views.practitioner_apply, name='practitioner_apply'),
    path('practitioners/apply/<int:step>/', views.practitioner_apply_step,
         name='practitioner_apply_step'),
    path('practitioners/apply/sent/', views.practitioner_apply_done,
         name='practitioner_apply_done'),
]
