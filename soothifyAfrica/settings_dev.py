"""Settings for running the site on your own machine.

settings.py holds the live server's configuration: DEBUG off, which switches on
the https redirect, HSTS, and fingerprinted static files served from a
collectstatic manifest. The development server speaks plain HTTP and has no
manifest, so with those settings it cannot serve the site at all -- the browser
is redirected to https and every page raises "Missing staticfiles manifest
entry".

Run locally with:

    python manage.py runserver --settings=soothifyAfrica.settings_dev

Nothing here is used in production, so settings.py can stay exactly as the
server needs it.
"""

from .settings import *  # noqa: F401,F403

DEBUG = True

# The hardening block in settings.py only runs when DEBUG is off, but these are
# set explicitly in case this file is imported over a configuration that has
# already applied them.
SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
LANGUAGE_COOKIE_SECURE = False

# Serve static files straight from static/, with no collectstatic step.
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Print the welcome email to the terminal instead of trying to send it.
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
