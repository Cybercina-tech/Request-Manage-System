"""
Iraniu — Global site config in every template.
Cached to avoid DB hit on every request (reduces SQLite lock contention).
"""

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import SiteConfiguration, TelegramBot

SITE_CONFIG_CACHE_KEY = 'site_config_singleton'
SITE_CONFIG_CACHE_TIMEOUT = 60

# Webhook health: active < 5 min (cyan), idle 5–60 min (gold), dead > 60 min or null (red)
WEBHOOK_ACTIVE_MINUTES = 5
WEBHOOK_IDLE_MINUTES = 60


def site_config(request):
    try:
        config = cache.get(SITE_CONFIG_CACHE_KEY)
    except Exception:
        config = None
    if config is None:
        config = SiteConfiguration.get_config()
        try:
            cache.set(SITE_CONFIG_CACHE_KEY, config, timeout=SITE_CONFIG_CACHE_TIMEOUT)
        except Exception:
            pass
    environment = getattr(settings, 'ENVIRONMENT', 'PROD')
    theme_preference = getattr(config, 'theme_preference', 'light') or 'light'
    return {'config': config, 'environment': environment, 'theme_preference': theme_preference}


def static_version(request):
    """Expose STATIC_VERSION for cache busting in templates."""
    return {'STATIC_VERSION': getattr(settings, 'STATIC_VERSION', '1')}


def webhook_health(request):
    """Neon health pulse for sidebar: last webhook ping and state (active/idle/dead)."""
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    try:
        from django.conf import settings
        env = getattr(settings, "ENVIRONMENT", "PROD")
        last = (
            TelegramBot.objects.filter(
                mode=TelegramBot.Mode.WEBHOOK,
                is_active=True,
                environment=env,
            )
            .exclude(last_webhook_received__isnull=True)
            .order_by("-last_webhook_received")
            .values_list("last_webhook_received", flat=True)
            .first()
        )
    except Exception:
        last = None
    now = timezone.now()
    if last is None:
        state, label = 'dead', 'No webhook ping yet'
    else:
        delta_min = (now - last).total_seconds() / 60
        if delta_min < WEBHOOK_ACTIVE_MINUTES:
            state, label = 'active', last.strftime('Last ping: %H:%M')
        elif delta_min < WEBHOOK_IDLE_MINUTES:
            state, label = 'idle', last.strftime('Last ping: %H:%M')
        else:
            state, label = 'dead', last.strftime('Last ping: %Y-%m-%d %H:%M')
    return {'webhook_health': {'last_ping': last, 'state': state, 'label': label}}
