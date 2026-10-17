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

    # The staff dashboard: signups, the library, sessions, recordings.
    path('dashboard/', include('dashboard.urls')),
]

# English is served at "/", Pidgin at "/pcm/".
urlpatterns += i18n_patterns(
    path('', include('home.urls')),
    prefix_default_language=False,
)

# What a request that cannot be answered gets. Only used when DEBUG is off,
# which is every deployment -- locally you still get the yellow page.
# home/errors.py answers /api/ paths with JSON and everything else with a
# page, so the app never has to parse HTML to find out what went wrong.
handler400 = 'home.errors.bad_request'
handler403 = 'home.errors.forbidden'
handler404 = 'home.errors.not_found'
handler500 = 'home.errors.server_error'
