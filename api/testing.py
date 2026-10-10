"""Shared scaffolding for the API's tests.

Every API app tests against a live-ish configuration, which the website's
settings are not built for: they force https and serve static files from a
hashed manifest, neither of which the test client can do.
"""

from django.core.cache import cache
from django.test import TestCase, override_settings

# Applied to every API test case.
PLAIN_API = {
    'SECURE_SSL_REDIRECT': False,
    'STORAGES': {
        'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
        'staticfiles': {
            'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage',
        },
    },
}


@override_settings(**PLAIN_API)
class ApiTestCase(TestCase):
    """A TestCase that starts with the throttles reset.

    DRF reads DEFAULT_THROTTLE_RATES once, when its throttle module is
    imported, so override_settings cannot switch the limits off. The counts
    themselves live in the cache, which outlives a test -- so a suite that
    signs in a few dozen times would throttle itself and fail somewhere in the
    middle, blaming whichever test happened to be twentieth. Clearing the
    cache keeps the real rates in force while giving each test its own budget.
    """

    def setUp(self):
        cache.clear()
        super().setUp()

    def signed_in_as(self, email, password):
        """The Authorization header for someone who has just signed in."""
        from django.urls import reverse

        response = self.client.post(
            reverse('api:accounts:login'),
            {'email': email, 'password': password},
            content_type='application/json',
        )
        return 'Bearer {0}'.format(response.json()['tokens']['access'])
