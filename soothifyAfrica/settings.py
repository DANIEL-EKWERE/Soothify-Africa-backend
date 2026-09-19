"""
Django settings for soothifyAfrica.

Local development needs no environment variables: DEBUG is on, the database is
SQLite and static files are served straight from static/. On Render (which sets
RENDER=true) the defaults flip to production-safe ones.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Hard-coded production values for a cPanel deployment.
SECRET_KEY = "soothify-africa-production-secret-key-2026-strong-enough-for-live-site"
DEBUG = False

ALLOWED_HOSTS = [
    "soothifyafrica.com.ng",
    "www.soothifyafrica.com.ng",
    "localhost",
    "127.0.0.1",
    "[::1]",
    "testserver",
]

CSRF_TRUSTED_ORIGINS = [
    "https://soothifyafrica.com",
    "https://www.soothifyafrica.com",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]


# Application definition

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    'home',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    # Must sit directly below SecurityMiddleware so it serves static files
    # before anything else does work on the request.
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    # after LocaleMiddleware: sends a visitor who chose Pidgin to the /pcm/ page
    'home.middleware.PreferredLanguageMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'soothifyAfrica.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.template.context_processors.i18n',
                'home.context_processors.site',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'soothifyAfrica.wsgi.application'


# Database
# Using SQLite for this cPanel deployment so no runtime environment variables
# are required. The database file lives in the project directory.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# Password validation
# https://docs.djangoproject.com/en/6.0/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]


# Internationalization
# https://docs.djangoproject.com/en/6.0/topics/i18n/

LANGUAGE_CODE = 'en'

# 'pcm' is the ISO 639-3 code for Nigerian Pidgin. Django has no built-in name for
# it, so it is declared here explicitly.
LANGUAGES = [
    ('en', 'English'),
    ('pcm', 'Pidgin'),
]

LOCALE_PATHS = [BASE_DIR / 'locale']

TIME_ZONE = 'UTC'

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/6.0/howto/static-files/

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]

# Where collectstatic writes. On the cPanel server that is the domain's document
# root, so LiteSpeed serves /static/ itself: it finds the file on disk before the
# request reaches Django, and it answers the byte-range requests browsers use
# for video (the Python app returned a 500 for those, so the hero video never
# played, and randomly 404'd other files). Elsewhere -- your laptop -- that
# folder does not exist, so files go to staticfiles/ and WhiteNoise serves them.
# If the domain's document root is not public_html, change this one path.
DOCUMENT_ROOT_STATIC = Path("/home/wdivczxg/public_html/static")
STATIC_ROOT = (
    DOCUMENT_ROOT_STATIC if DOCUMENT_ROOT_STATIC.parent.is_dir() else BASE_DIR / "staticfiles"
)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        # Hashed filenames + precompressed copies in production, so assets can be
        # cached forever. Plain storage in development, which needs no manifest.
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        )
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# Production hardening — only when DEBUG is off, so local development is untouched.
if not DEBUG:
    # Render terminates TLS at its proxy and forwards the original scheme here.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True

    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    LANGUAGE_COOKIE_SECURE = True

    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = "DENY"

    # One year of HSTS. Preload is left off deliberately: submitting to the
    # preload list is effectively irreversible, so opt in only when you are sure
    # every subdomain will serve HTTPS forever.
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
