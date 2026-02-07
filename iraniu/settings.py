"""
Iraniu
Django settings.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-change-in-production-iraniu')

DEBUG = False

ALLOWED_HOSTS = [
    "request.iraniu.uk",
    "www.request.iraniu.uk",
    "localhost",
    "127.0.0.1",
]

CSRF_TRUSTED_ORIGINS = [
    "https://request.iraniu.uk",
    "http://request.iraniu.uk",
]

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'core.middleware.LoginRequiredMiddleware',
    'core.middleware.ApiKeyAuthMiddleware',
]

ROOT_URLCONF = 'iraniu.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.site_config',
                'core.context_processors.static_version',
            ],
        },
    },
]

WSGI_APPLICATION = 'iraniu.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')  # folder for collected static files
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# Cache busting for static assets (icons, CSS)
STATIC_VERSION = os.environ.get('STATIC_VERSION', '1')

<<<<<<< HEAD
# Media for user-uploaded and generated images (Instagram)
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = os.environ.get('MEDIA_URL', '/media/')
# Public base URL for Instagram image URLs (required; set in .env)
INSTAGRAM_BASE_URL = os.environ.get('INSTAGRAM_BASE_URL', '')
=======
# Security defaults (minimal, no HSTS yet)
SESSION_COOKIE_SECURE = False  # enable later if SSL forced
CSRF_COOKIE_SECURE = False     # enable later if SSL forced
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
>>>>>>> 29451ba321b94dab344d06bf8c4e17cff1d87f44

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# Telegram Bot Runner
# polling: long-poll getUpdates (dev, no HTTPS). webhook: validate + health only (prod with HTTPS).
TELEGRAM_MODE = os.environ.get('TELEGRAM_MODE', 'polling').lower()
if TELEGRAM_MODE not in ('polling', 'webhook'):
    TELEGRAM_MODE = 'polling'

<<<<<<< HEAD
# Auto-start bots with Django (runserver, gunicorn, WSGI). Set env ENABLE_AUTO_BOTS=false to disable (e.g. tests/migrations).
ENABLE_AUTO_BOTS = os.environ.get('ENABLE_AUTO_BOTS', 'true').lower() in ('true', '1', 'yes', 'on')

# Security headers (only when not debugging)
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
=======
>>>>>>> 29451ba321b94dab344d06bf8c4e17cff1d87f44
