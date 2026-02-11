"""
Iraniu — Global site config in every template.
Cached to avoid DB hit on every request (reduces SQLite lock contention).
"""

import logging
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from .models import SiteConfiguration, SystemStatus, TelegramBot

logger = logging.getLogger(__name__)

SITE_CONFIG_CACHE_KEY = 'site_config_singleton'
SITE_CONFIG_CACHE_TIMEOUT = 60

# Webhook health: active < 5 min (cyan), idle 5–60 min (gold), dead > 60 min or null (red)
WEBHOOK_ACTIVE_MINUTES = 5
WEBHOOK_IDLE_MINUTES = 60

# System watchdog: if last_heartbeat older than this, worker is OFFLINE
WORKER_OFFLINE_THRESHOLD_SEC = 120  # 2 minutes
SYSTEM_STATUS_CACHE_KEY = 'system_status_worker_online'
SYSTEM_STATUS_CACHE_TIMEOUT = 30  # Cache result 30s to avoid DB on every request


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


def system_watchdog(request):
    """
    Expose worker_offline and active_errors for templates (red banner, health widget).
    Cached 30s to avoid DB hit on every request.
    """
    if not getattr(request, 'user', None) or not request.user.is_authenticated:
        return {}
    try:
        cached = cache.get(SYSTEM_STATUS_CACHE_KEY)
        if cached is not None:
            return cached
    except Exception:
        pass
    try:
        status = SystemStatus.get_status()
        last = status.last_heartbeat
        now = timezone.now()
        if last is None:
            worker_offline = True
        else:
            delta_sec = (now - last).total_seconds()
            worker_offline = delta_sec > WORKER_OFFLINE_THRESHOLD_SEC
        active_errors = getattr(status, 'active_errors', None) or []
        if not isinstance(active_errors, list):
            active_errors = []
        result = {
            'worker_offline': worker_offline,
            'worker_last_heartbeat': last,
            'system_active_errors': active_errors,
        }
        try:
            cache.set(SYSTEM_STATUS_CACHE_KEY, result, timeout=SYSTEM_STATUS_CACHE_TIMEOUT)
        except Exception:
            pass
        return result
    except Exception:
        return {'worker_offline': True, 'worker_last_heartbeat': None, 'system_active_errors': []}


NOTIFICATIONS_CACHE_KEY = 'navbar_notifications'
NOTIFICATIONS_CACHE_TIMEOUT = 20

# Link for "System Health" (dashboard shows the health widget)
SYSTEM_HEALTH_LINK = '/dashboard/'


def notifications(request):
    """
    Always expose notification_unread_count and recent_notifications in every template.
    - Anonymous: 0 and [].
    - Authenticated: latest 5 notifications from DB; unread count.
    """
    # Always provide keys so templates never see undefined variables
    default_result = {'notification_unread_count': 0, 'recent_notifications': []}

    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated:
        return default_result

    try:
        cached = cache.get(NOTIFICATIONS_CACHE_KEY)
        if cached is not None:
            recent = cached['recent_notifications']
            unread_count = cached['notification_unread_count']
            return {'notification_unread_count': unread_count, 'recent_notifications': recent}
    except Exception as e:
        logger.debug("notifications cache get: %s", e)

    try:
        from .models import Notification
        unread_count = Notification.objects.filter(is_read=False).count()
        recent = list(
            Notification.objects.order_by('-created_at')[:5].values(
                'id', 'level', 'message', 'link', 'is_read', 'created_at'
            )
        )
        for n in recent:
            if n.get('created_at'):
                n['created_at'] = n['created_at'].strftime('%b %d, %H:%M')

        result = {'notification_unread_count': unread_count, 'recent_notifications': recent}
        try:
            cache.set(NOTIFICATIONS_CACHE_KEY, result, timeout=NOTIFICATIONS_CACHE_TIMEOUT)
        except Exception:
            pass
        return result
    except Exception as e:
        logger.warning("notifications context processor failed: %s", e)
        return default_result
