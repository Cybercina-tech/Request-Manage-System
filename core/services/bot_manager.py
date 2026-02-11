"""
Iraniu — Webhook activation and bot management.
Default bot auto-provisioning (idempotent). activate_webhook delegates to bot_lifecycle.
"""

import logging

from core.models import TelegramBot
from core.services import bot_lifecycle

logger = logging.getLogger(__name__)

DEFAULT_BOT_NAME = "Iraniu Official Ads Bot"


def ensure_default_bot():
    """
    Idempotent: ensure exactly one TelegramBot has is_default=True.
    If none exists, create "Iraniu Main Bot" with empty token.
    Call from AppConfig.ready() or after deploy.
    """
    if TelegramBot.objects.filter(is_default=True).exists():
        return
    bot = TelegramBot(
        name=DEFAULT_BOT_NAME,
        is_default=True,
        is_active=True,
        status=TelegramBot.Status.OFFLINE,
        mode=TelegramBot.Mode.WEBHOOK,
    )
    bot.set_token("")
    bot.save()
    logger.info("Default Telegram bot created: %s (pk=%s)", DEFAULT_BOT_NAME, bot.pk)


def activate_webhook(bot: TelegramBot):
    """
    Activate webhook mode for a bot. Delegates to bot_lifecycle.activate_webhook.

    Returns:
        (success: bool, message: str, full_url: str or None)
    """
    return bot_lifecycle.activate_webhook(bot)


def health_check_default_bot():
    """
    Run a health check for the default bot in the current environment.
    """
    from django.conf import settings
    from django.utils import timezone
    from core.services.telegram import test_telegram_connection

    env = getattr(settings, "ENVIRONMENT", "PROD")
    bot = (
        TelegramBot.objects.filter(environment=env, is_active=True)
        .order_by("-is_default")
        .first()
    )
    if not bot:
        return
    token = bot.get_decrypted_token()
    if not token:
        return
    ok, msg = test_telegram_connection(token)
    if ok:
        TelegramBot.objects.filter(pk=bot.pk).update(
            status=TelegramBot.Status.ONLINE,
            last_heartbeat=timezone.now(),
            last_error="",
        )
    else:
        TelegramBot.objects.filter(pk=bot.pk).update(
            status=TelegramBot.Status.ERROR,
            last_error=(msg or "Startup health check failed")[:500],
        )


# Pulse: Cyan = responding, Gold = idle (no messages 5min–1hr), Red = dead or invalid
WEBHOOK_ACTIVE_MINUTES = 5
WEBHOOK_IDLE_MINUTES = 60   # Gold pulse: webhook set but no messages in up to 1 hour


def webhook_pulse_for_bot(bot):
    """
    Return (state, label) for a bot's last_webhook_received.
    state: 'active' (cyan) | 'idle' (gold) | 'dead' (red)
    """
    from django.utils import timezone
    last = getattr(bot, "last_webhook_received", None)
    if last is None:
        return "dead", "No webhook ping yet"
    now = timezone.now()
    delta_min = (now - last).total_seconds() / 60
    if delta_min < WEBHOOK_ACTIVE_MINUTES:
        return "active", last.strftime("Last ping: %H:%M")
    if delta_min < WEBHOOK_IDLE_MINUTES:
        return "idle", last.strftime("Last ping: %H:%M")
    return "dead", last.strftime("Last ping: %Y-%m-%d %H:%M")
