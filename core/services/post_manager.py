"""
Iraniu — Distribute approved ads: Telegram via DeliveryService (professional caption), Instagram via queue or direct.
Telegram is always sent through DeliveryService.send(ad, 'telegram_channel') so only the professional
caption (🚀 #آگهی_جدید) is used. Single-execution guard is in DeliveryService.

NOTE: For automatic distribution on approval, see DeliveryService in core/services/delivery.py.
This module's distribute_ad() is for the manual "Preview & Publish" button; it delegates Telegram to DeliveryService.
"""

import logging
from typing import Optional

from django.conf import settings

from core.models import AdRequest, DeliveryLog, InstagramSettings, SiteConfiguration, TelegramChannel
from core.notifications import send_notification
from core.services.image_engine import ensure_feed_image
from core.services.delivery import DeliveryService

logger = logging.getLogger(__name__)


def _channel_from_site_config():
    """
    If Site Configuration has an active default channel, return (chat_id, token, title) for it.
    Otherwise return (None, None, None).
    """
    config = SiteConfiguration.get_config()
    if not config.is_channel_active or not (config.telegram_channel_id or "").strip():
        return None, None, None
    bot = config.default_telegram_bot
    if not bot or not bot.is_active:
        return None, None, None
    try:
        token = bot.get_decrypted_token()
    except Exception:
        return None, None, None
    if not token:
        return None, None, None
    try:
        chat_id = int(config.telegram_channel_id.strip())
    except (ValueError, TypeError):
        return None, None, None
    return chat_id, token, (config.telegram_channel_title or "").strip()


def get_default_channel() -> Optional[TelegramChannel]:
    """
    Return the default channel for the current environment for display/preview.
    Prefer Site Configuration when it has an active channel; else the TelegramChannel marked default.
    """
    config = SiteConfiguration.get_config()
    if config.is_channel_active and (config.telegram_channel_id or "").strip() and config.default_telegram_bot:
        # Return a minimal channel-like object for templates (has .channel_id, .title, .bot_connection)
        class _ConfigChannel:
            pass
        ch = _ConfigChannel()
        ch.channel_id = config.telegram_channel_id.strip()
        ch.title = config.telegram_channel_title or "Default (Settings)"
        ch.bot_connection = config.default_telegram_bot
        return ch
    env = getattr(settings, "ENVIRONMENT", "PROD")
    return (
        TelegramChannel.objects.filter(
            is_default=True,
            is_active=True,
            bot_connection__environment=env,
        )
        .select_related("bot_connection")
        .first()
    )


def distribute_ad(ad_obj: AdRequest) -> bool:
    """
    Manual distribution (from Preview & Publish page):
    1. Telegram: delegated to DeliveryService.send(ad, 'telegram_channel') — professional caption only, single-execution guard there.
    2. Generate Feed image for Instagram if needed.
    3. Instagram Feed + Story: create_container + publish_media, or queue when enable_instagram_queue is ON.

    Returns True if at least Telegram or Instagram succeeded, False otherwise.
    """
    if not isinstance(ad_obj, AdRequest):
        logger.warning("post_manager.distribute_ad: invalid ad type")
        return False
    if ad_obj.status != AdRequest.Status.APPROVED:
        logger.debug("post_manager.distribute_ad: ad %s not approved, status=%s", ad_obj.uuid, ad_obj.status)
        return False

    # Generate Feed image so DeliveryService and Instagram have it
    ensure_feed_image(ad_obj)

    # Telegram Channel: single path — DeliveryService (professional caption, guard against duplicate post)
    telegram_ok = DeliveryService.send(ad_obj, 'telegram_channel')
    if not telegram_ok:
        logger.warning("post_manager.distribute_ad: Telegram delivery failed for ad %s", ad_obj.uuid)
        send_notification(
            "error",
            "Telegram post failed for ad. Check bot token and channel permissions.",
            link="/settings/hub/telegram/",
        )

    # ──────────────────────────────────────────────
    # Instagram Feed + Story (strict separation: Feed URL vs Story URL)
    # When enable_instagram_queue is ON: queue instead of posting immediately.
    # ──────────────────────────────────────────────
    instagram_ok = False
    config = SiteConfiguration.get_config()
    if not getattr(config, 'is_instagram_enabled', False):
        logger.info("post_manager.distribute_ad: Instagram disabled, skipping for ad %s", ad_obj.uuid)
    else:
        ig_settings = InstagramSettings.get_settings()
        if getattr(ig_settings, 'enable_instagram_queue', False):
            # Queue for scheduler; do not call Instagram API here
            for ch in ('instagram', 'instagram_story'):
                DeliveryLog.objects.create(ad=ad_obj, channel=ch, status=DeliveryLog.DeliveryStatus.QUEUED)
            ad_obj.instagram_queue_status = 'queued'
            ad_obj.save(update_fields=['instagram_queue_status'])
            logger.info("post_manager.distribute_ad: Instagram queue ON, ad %s queued", ad_obj.uuid)
            instagram_ok = True
        else:
            # Use DeliveryService for Instagram so token check, 30s delay, and container status are applied
            try:
                instagram_ok = DeliveryService.send(ad_obj, 'instagram')
                story_ok = DeliveryService.send(ad_obj, 'instagram_story')
                if story_ok:
                    instagram_ok = True
                if not instagram_ok:
                    send_notification(
                        "error",
                        "Instagram post or story failed. Check token and that image URL is public.",
                        link="/settings/hub/instagram/",
                    )
            except Exception as exc:
                logger.exception("post_manager.distribute_ad: Instagram delivery crashed ad=%s: %s", ad_obj.uuid, exc)

    return telegram_ok or instagram_ok
