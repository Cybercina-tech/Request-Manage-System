"""Iraniu Django settings."""

import os
from pathlib import Path
from typing import Iterable

from django.core.exceptions import ImproperlyConfigured

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - optional dependency guard
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent.parent

if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _env_list(name: str, default: Iterable[str] | None = None) -> list[str]:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return list(default or [])
    return [item.strip() for item in raw.split(",") if item.strip()]


DEBUG = _env_bool("DJANGO_DEBUG", default=True)  # default True for local; set False in production
SECRET_KEY = (os.environ.get("DJANGO_SECRET_KEY") or "").strip() or "dev-change-in-production-iraniu"
if not DEBUG and not (os.environ.get("DJANGO_SECRET_KEY") or "").strip():
    raise ImproperlyConfigured("DJANGO_SECRET_KEY must be set when DJANGO_DEBUG is false.")

ALLOWED_HOSTS = _env_list(
    "DJANGO_ALLOWED_HOSTS",
    default=[
        "request.iraniu.uk",
        "www.request.iraniu.uk",
        "localhost",
        "127.0.0.1",
    ],
)

CSRF_TRUSTED_ORIGINS = _env_list(
    "DJANGO_CSRF_TRUSTED_ORIGINS",
    default=["https://request.iraniu.uk", "http://request.iraniu.uk"],
)

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
    'django.middleware.locale.LocaleMiddleware',
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
                'core.context_processors.system_watchdog',
                'core.context_processors.notifications',
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

# Default language: English. Use landing page button or set_language to switch.
LANGUAGE_CODE = 'en'
LANGUAGES = [
    ('en', 'English'),
    ('fa', 'فارسی'),
]
LOCALE_PATHS = [BASE_DIR / 'locale']
TIME_ZONE = 'Asia/Tehran'
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

# Media: user-uploaded and generated images (Instagram, ads). Public read-only; no directory listing.
# Django static() serve does not list directories (404 for directory paths). In production (Nginx/Apache), set autoindex off.
MEDIA_URL = os.environ.get('MEDIA_URL', '/media/')
MEDIA_ROOT = BASE_DIR / 'media'
# Public base URL for Instagram image URLs (required; set in .env)
INSTAGRAM_BASE_URL = os.environ.get('INSTAGRAM_BASE_URL', '')

# Instagram OAuth — redirect_uri must match exactly what is configured in Meta Developer portal.
# Canonical value (used when building auth URL and when exchanging code for tokens):
INSTAGRAM_REDIRECT_URI = (
    (os.environ.get('INSTAGRAM_REDIRECT_URI') or '').strip()
    or 'https://request.iraniu.uk/settings/hub/instagram/callback/'
)
# Manual OAuth (no social-auth-app-django). All Meta URLs must be HTTPS:
INSTAGRAM_GRAPH_API_BASE = 'https://graph.facebook.com/v18.0'
INSTAGRAM_OAUTH_AUTHORIZATION_URL = 'https://www.facebook.com/v18.0/dialog/oauth'
# Instagram webhook: verify token must match the value you set in Meta Developer portal.
INSTAGRAM_WEBHOOK_VERIFY_TOKEN = os.environ.get('INSTAGRAM_WEBHOOK_VERIFY_TOKEN', 'my_secret_123').strip() or 'my_secret_123'

# اجبار جنگو به تشخیص HTTPS از طریق هدرهای cPanel/پروکسی
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')

# Security defaults
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
SECURE_SSL_REDIRECT = _env_bool("DJANGO_SECURE_SSL_REDIRECT", default=not DEBUG)
SECURE_HSTS_SECONDS = int((os.environ.get("DJANGO_SECURE_HSTS_SECONDS") or "31536000").strip() or "31536000") if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/dashboard/'
LOGOUT_REDIRECT_URL = '/'

# Environment: PROD (cPanel/live) or DEV (local). Used to select which Telegram bot(s) run and which channel to post to.
_ENV_RAW = (os.environ.get('ENVIRONMENT') or '').strip().upper()
if _ENV_RAW in ('PROD', 'DEV'):
    ENVIRONMENT = _ENV_RAW
else:
    ENVIRONMENT = 'PROD' if not DEBUG else 'DEV'

# Telegram Bot Runner
# polling (default): runbots starts getUpdates workers. webhook: use setWebhook + HTTPS; runbots still starts polling workers for bots with mode=Polling in DB.
TELEGRAM_MODE = os.environ.get('TELEGRAM_MODE', 'polling').lower()
if TELEGRAM_MODE not in ('polling', 'webhook'):
    TELEGRAM_MODE = 'polling'

# Auto-start bots with Django when TELEGRAM_MODE is polling. DISABLED for cPanel — use Cron Job with 'python manage.py runbots' instead.
ENABLE_AUTO_BOTS = False  # Manual execution only via 'python manage.py runbots' (Cron Job)

# Logging — separate Instagram API log for monitoring posts, OAuth, and errors.
LOGS_DIR = BASE_DIR / 'logs'
LOGS_DIR.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{asctime} [{levelname}] {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'instagram_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOGS_DIR / 'instagram.log'),
            'maxBytes': 5 * 1024 * 1024,  # 5 MB
            'backupCount': 3,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
        'bot_log_file': {
            'class': 'logging.handlers.RotatingFileHandler',
            'filename': str(LOGS_DIR / 'bot_log.txt'),
            'maxBytes': 2 * 1024 * 1024,  # 2 MB
            'backupCount': 2,
            'formatter': 'verbose',
            'encoding': 'utf-8',
        },
    },
    'loggers': {
        'core.services.instagram': {
            'handlers': ['console', 'instagram_file'],
            'level': 'INFO',
            'propagate': False,
        },
        'core.instagram.bot': {
            'handlers': ['bot_log_file', 'console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}
