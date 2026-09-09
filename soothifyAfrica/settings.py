"""
Django settings for soothifyAfrica.

Local development needs no environment variables: DEBUG is on, the database is
SQLite and static files are served straight from static/. On Render (which sets
RENDER=true) the defaults flip to production-safe ones.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def env_flag(name, default=False):
    """Read a boolean-ish environment variable."""
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def env_list(name):
    return [v.strip() for v in os.environ.get(name, "").split(",") if v.strip()]


# Render sets RENDER=true in every service, which is what flips the defaults
# below. Nothing else in the file needs to know where it is running.
ON_RENDER = env_flag("RENDER")

DEV_SECRET_KEY = "django-insecure-$r$x!#-6g$u4e7#g1zp)168oa15*=pvh74$t&hubu(eb=pkome"
SECRET_KEY = 'django-insecure-$r$x!#-6g$u4e7#g1zp)168oa15*=pvh74$t&hubu(eb=pkome' #os.environ.get("SECRET_KEY", DEV_SECRET_KEY)
"""
if ON_RENDER and SECRET_KEY == DEV_SECRET_KEY:
    # Fail the deploy loudly rather than serve production with the throwaway key.
    raise RuntimeError(
        "SECRET_KEY is unset on Render. Add it as an environment variable "
        "(render.yaml generates one automatically)."
    )
    """

DEBUG = env_flag("DJANGO_DEBUG", default=not ON_RENDER)

# Render publishes the service's own hostname; extra domains go in ALLOWED_HOSTS.
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")
if os.environ.get("RENDER_EXTERNAL_HOSTNAME"):
    ALLOWED_HOSTS.append(os.environ["RENDER_EXTERNAL_HOSTNAME"])
if DEBUG:
    ALLOWED_HOSTS += ["localhost", "127.0.0.1", "[::1]", "testserver"]

# Django needs the scheme here, and only for hosts that actually serve HTTPS.
CSRF_TRUSTED_ORIGINS = [
    f"https://{host}"
    for host in ALLOWED_HOSTS
    if not host.startswith(("localhost", "127.", "[", "testserver"))
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
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'soothifyAfrica.wsgi.application'


# Database
# https://docs.djangoproject.com/en/6.0/ref/settings/#databases

# Postgres when DATABASE_URL is present (Render sets it when a database is
# attached), otherwise the local SQLite file.
#
# WARNING: Render's filesystem is ephemeral. On SQLite, anything written at
# runtime — admin users, sessions — is wiped on every deploy and restart.
# Attach a Postgres instance before storing anything you care about.
if os.environ.get("DATABASE_URL"):
    import dj_database_url

    DATABASES = {
        "default": dj_database_url.parse(
            os.environ["DATABASE_URL"],
            conn_max_age=600,
            conn_health_checks=True,
        )
    }
else:
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

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
# collectstatic writes here; WhiteNoise serves from it. Regenerated every build,
# so it is gitignored.
STATIC_ROOT = BASE_DIR / "staticfiles"

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
