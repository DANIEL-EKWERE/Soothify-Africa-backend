"""
URL configuration for soothifyAfrica project.
"""
from django.conf.urls.i18n import i18n_patterns
from django.contrib import admin
from django.urls import include, path

# Not language-prefixed: the admin, and the endpoint the language switcher posts to.
urlpatterns = [
    path('admin/', admin.site.urls),
    path('i18n/', include('django.conf.urls.i18n')),
]

# English is served at "/", Pidgin at "/pcm/".
urlpatterns += i18n_patterns(
    path('', include('home.urls')),
    prefix_default_language=False,
)
