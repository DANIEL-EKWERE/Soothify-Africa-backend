"""Every mobile endpoint hangs off /api/v1/.

Outside i18n_patterns on purpose: the app sends its language as a header or a
field, and a URL that changes shape per language is a poor contract for a
client. See soothifyAfrica/urls.py.
"""

from django.conf import settings
from django.urls import include, path
from rest_framework_simplejwt.views import TokenRefreshView, TokenVerifyView

from . import media, views

app_name = 'api'

urlpatterns = [
    path('health/', views.health, name='health'),

    # Tokens. Obtaining one lives in accounts (it returns the profile too);
    # refresh and verify are SimpleJWT's own.
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='token-refresh'),
    path('auth/token/verify/', TokenVerifyView.as_view(), name='token-verify'),

    path('auth/', include('accounts.urls')),
    path('content/', include('content.urls')),
    path('wellbeing/', include('wellbeing.urls')),
    path('spaces/', include('spaces.urls')),
    path('corporate/', include('corporate.urls')),
    path('notifications/', include('notifications.urls')),
    path('', include('experts.urls')),
    path('billing/', include('billing.urls')),
    path('recordings/', include('recordings.urls')),

    # Only used when media is served from this machine (cPanel); on AWS the
    # storage backend signs its own URLs and nothing routes here.
    # <path:…>, not <str:…>: a signed token carries the file's own path, and
    # every stored file sits in a folder ("covers/x.jpg"), so a converter
    # that stops at a slash never matches.
    path('media/<path:token>/', media.serve_media, name='media'),
]

# On hold -- see FEATURES in settings.py.
if settings.FEATURES.get('community'):
    urlpatterns.append(path('community/', include('community.urls')))
