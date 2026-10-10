"""
URL configuration for soothifyAfrica project.
"""
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.urls import include, path

# Not language-prefixed: the admin, the language switcher's endpoint, and the API.
urlpatterns = [
    path('admin/', admin.site.urls),
    path('i18n/', include('django.conf.urls.i18n')),

    # The Flutter app. Never language-prefixed: a mobile client should not have
    # to guess which URL shape it will get.
    path('api/v1/', include('api.urls')),
]

# English is served at "/", Pidgin at "/pcm/".
urlpatterns += i18n_patterns(
    path('', include('home.urls')),
    prefix_default_language=False,
)
