"""
Iraniu — Bot lifecycle: switch between Webhook and Polling mode.

- activate_webhook(bot): Build HTTPS URL from production_base_url, set webhook via API,
  stop any running polling worker for this bot.
- activate_polling(bot): Delete webhook, set mode to polling; polling is started by
  runbots command or AppConfig when not in a restricted environment (e.g. not collectstatic/migrate).
"""

import logging
from django.urls import reverse

from core.models import TelegramBot, SiteConfiguration
from core.services.telegram_client import delete_webhook, set_webhook

logger = logging.getLogger(__name__)


def get_production_base_url() -> str:
    """Return production_base_url from SiteConfiguration, validated (must be HTTPS)."""
    config = SiteConfiguration.get_config()
    base = (config.production_base_url or "").strip().rstrip("/")
    if base and not base.startswith("https://"):
        return ""
    return base


def activate_webhook(bot: TelegramBot) -> tuple:
    """
    Activate webhook mode for a bot.

    - Builds URL: {production_base_url}/telegram/webhook/{bot.webhook_secret_token}/
    - Calls client.set_webhook(url, secret_token=webhook_secret_token)
    - Stops any running polling for this bot (sets requested_action=STOP so runbots applies it)
    - Saves bot.webhook_url, bot.mode=WEBHOOK

    Returns:
        (success: bool, message: str, full_url: str | None)
    """
    base = get_production_base_url()
    if not base:
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
    bot.requested_action = TelegramBot.RequestedAction.STOP  # so runbots stops polling worker
    bot.save(update_fields=["webhook_url", "mode", "requested_action"])
    logger.info("Webhook activated bot_id=%s url=%s", bot.pk, full_url)
    return True, "Webhook activated.", full_url


def activate_polling(bot: TelegramBot) -> tuple:
    """
    Switch bot to polling mode: delete webhook (with drop_pending_updates), set mode to POLLING.

    Does NOT start the polling process; that is done by:
    - `python manage.py runbots` (or runbots with --bot-id=N), or
    - AppConfig.ready when TELEGRAM_MODE=polling and not in a management command.
    In restricted environments (e.g. cPanel WSGI), only webhook mode is used; no threads are started.

    Returns:
        (success: bool, message: str)
    """
    token = bot.get_decrypted_token()
    if not token:
        return False, "No bot token configured."

    ok_del, err = delete_webhook(token, drop_pending_updates=True)
    if not ok_del:
        logger.warning("activate_polling: delete_webhook failed for bot_id=%s: %s", bot.pk, err)
        return False, err or "deleteWebhook failed"

    bot.webhook_url = ""
    bot.mode = TelegramBot.Mode.POLLING
    bot.save(update_fields=["webhook_url", "mode"])
    logger.info("Polling mode activated for bot_id=%s (run runbots to start worker)", bot.pk)
    return True, "Webhook removed. Run 'python manage.py runbots' to start polling."
