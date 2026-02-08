"""
Iraniu — Webhook activation and bot management.
Builds secret webhook URL and registers it with Telegram.
Default bot auto-provisioning (idempotent).
"""

import logging
from django.urls import reverse

from core.models import TelegramBot, SiteConfiguration
from core.services.telegram_client import delete_webhook, set_webhook

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
    Activate webhook mode for a bot: delete existing webhook, then set new one
    using production_base_url + primary path telegram_webhook_by_token (UUID).
    URL: /telegram/webhook/<webhook_secret_token>/.
    Uses webhook_secret_token for the path and as Telegram secret_token header.

    Returns:
        (success: bool, message: str, full_url: str or None)
    """
    config = SiteConfiguration.get_config()
    base = (config.production_base_url or "").strip().rstrip("/")
    if not base or not base.startswith("https://"):
        return False, "Set production_base_url in Site Configuration (HTTPS only).", None
    token = bot.get_decrypted_token()
    if not token:
        return False, "No bot token configured.", None
    full_url = f"{base}{reverse('telegram_webhook_by_token', kwargs={'webhook_secret_token': bot.webhook_secret_token})}"
    ok_del, _ = delete_webhook(token)
    if not ok_del:
        logger.warning("activate_webhook: delete_webhook failed for bot_id=%s", bot.pk)
    ok_set, err = set_webhook(
        token,
        full_url,
        secret_token=str(bot.webhook_secret_token),
    )
    if not ok_set:
        return False, err or "setWebhook failed", full_url
    bot.webhook_url = full_url
    bot.mode = TelegramBot.Mode.WEBHOOK
    bot.save(update_fields=["webhook_url", "mode"])
    logger.info("Webhook activated bot_id=%s url=%s", bot.pk, full_url)
    return True, "Webhook activated.", full_url


def health_check_default_bot():
    """
    Run a health check for the default bot: test connection and update status/last_heartbeat.
    Call on startup (e.g. from apps.ready or post_migrate) or periodically.
    """
    from django.utils import timezone
    from core.services.telegram import test_telegram_connection

    bot = TelegramBot.objects.filter(is_default=True).first()
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
