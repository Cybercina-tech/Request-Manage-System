"""
Iraniu
Django settings.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('DJANGO_SECRET_KEY', 'dev-change-in-production-iraniu')

DEBUG = True

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
                'core.context_processors.webhook_health',
            ],
        },
    },
]

WSGI_APPLICATION = 'iraniu.wsgi.application'

# SQLite: timeout reduces lock wait; WAL via connection_created in core.apps
# For production concurrency, consider PostgreSQL (no file-level locking).
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
        'OPTIONS': {
            'timeout': 15,
        },
        'CONN_MAX_AGE': 0,
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
# منبع فایل‌های استاتیک (collectstatic از این‌ها داخل STATIC_ROOT کپی می‌کند)
STATICFILES_DIRS = [BASE_DIR / "static"]
# مقصد collectstatic؛ نباید داخل STATICFILES_DIRS باشد (حلقه بی‌نهایت و RecursionError)
STATIC_ROOT = BASE_DIR / 'staticfiles'

# فیوز ایمنی: STATIC_ROOT نباید با هیچ مسیر منبع تداخل داشته باشد
_static_root_r = STATIC_ROOT.resolve()
for _d in STATICFILES_DIRS:
    _dr = Path(_d).resolve()
    if _static_root_r == _dr:
        raise ValueError("STATIC_ROOT must not equal any STATICFILES_DIRS entry.")
    try:
        # Python 3.9+: روش مستقیم
        if _static_root_r.is_relative_to(_dr) or _dr.is_relative_to(_static_root_r):
            raise ValueError("STATIC_ROOT must not be inside STATICFILES_DIRS or vice versa.")
    except AttributeError:
        # Python < 3.9: با relative_to تست می‌کنیم
        try:
            _static_root_r.relative_to(_dr)
            raise ValueError("STATIC_ROOT must not be inside STATICFILES_DIRS.")
        except ValueError:
            try:
                _dr.relative_to(_static_root_r)
                raise ValueError("STATICFILES_DIRS entry must not be inside STATIC_ROOT.")
            except ValueError:
                pass

# Avoid manifest strictness in development: use plain storage when DEBUG so missing
# manifest entries don't cause 500s. Production uses manifest; run collectstatic with
# DEBUG=False (e.g. in deploy) to build staticfiles.json.
if DEBUG:
    STATICFILES_STORAGE = 'django.contrib.staticfiles.storage.StaticFilesStorage'
else:
    STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

# Cache busting for static assets (icons, CSS)
STATIC_VERSION = os.environ.get('STATIC_VERSION', '1')

# Cache: LocMemCache is process-local; avoids DB for rate limits and SiteConfig cache.
# For multi-worker production, use Redis/Memcached if shared rate limiting is needed.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
        'LOCATION': 'iraniu-default',
        'OPTIONS': {'MAX_ENTRIES': 1000},
    }
}

# Media for user-uploaded and generated images (Instagram)
MEDIA_URL = os.environ.get('MEDIA_URL', '/media/')
MEDIA_ROOT = BASE_DIR / 'media'
# Public base URL for Instagram image URLs (required; set in .env)
INSTAGRAM_BASE_URL = os.environ.get('INSTAGRAM_BASE_URL', '')

# Security defaults (minimal, no HSTS yet)
SESSION_COOKIE_SECURE = False  # enable later if SSL forced
CSRF_COOKIE_SECURE = False     # enable later if SSL forced
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# Telegram Bot Runner
# polling (default): runbots starts getUpdates workers. webhook: use setWebhook + HTTPS; runbots still starts polling workers for bots with mode=Polling in DB.
TELEGRAM_MODE = os.environ.get('TELEGRAM_MODE', 'polling').lower()
if TELEGRAM_MODE not in ('polling', 'webhook'):
    TELEGRAM_MODE = 'polling'

# Auto-start bots with Django when TELEGRAM_MODE is polling. Set ENABLE_AUTO_BOTS=false to disable (e.g. tests/migrations).
ENABLE_AUTO_BOTS = os.environ.get('ENABLE_AUTO_BOTS', 'true').lower() in ('true', '1', 'yes', 'on')

# Security headers (only when not debugging)
if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
    X_FRAME_OPTIONS = 'DENY'
