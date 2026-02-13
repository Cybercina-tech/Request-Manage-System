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
from core.services.image_engine import ensure_feed_image, ensure_story_image
from core.services.instagram_client import create_container, publish_media
from core.services.instagram_api import get_absolute_media_url
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
    feed_url = get_absolute_media_url(ad_obj.generated_image) if ad_obj.generated_image else None

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
            from core.services.instagram import InstagramService
            instagram_caption = InstagramService.format_caption(ad_obj, lang='fa')[:2200]
            try:
                if feed_url:
                    result = create_container(feed_url, instagram_caption, is_story=False)
                    if result.get("success") and result.get("creation_id"):
                        pub = publish_media(result["creation_id"])
                        if pub.get("success"):
                            instagram_ok = True
                            ad_obj.instagram_post_id = pub.get("id", "")
                            ad_obj.is_instagram_published = True
                            ad_obj.save(update_fields=["instagram_post_id", "is_instagram_published"])
                            logger.info("post_manager.distribute_ad: Instagram Feed published for ad %s", ad_obj.uuid)
                        else:
                            msg = pub.get("message") or "Unknown error"
                            logger.warning("post_manager.distribute_ad: Instagram publish failed: %s", msg)
                            send_notification(
                                "error",
                                f"Instagram post failed: {msg}. Click to refresh token.",
                                link="/settings/hub/instagram/",
                                add_to_active_errors=("token" in msg.lower() or "expired" in msg.lower()),
                            )
                    else:
                        msg = result.get("message") or "Unknown error"
                        logger.warning("post_manager.distribute_ad: Instagram container failed: %s", msg)
                        send_notification(
                            "error",
                            f"Instagram container failed: {msg}. Click to fix.",
                            link="/settings/hub/instagram/",
                        )
                ensure_story_image(ad_obj)
                story_url = get_absolute_media_url(ad_obj.generated_story_image) if ad_obj.generated_story_image else None
                if story_url:
                    logger.info("post_manager.distribute_ad: Sending Story URL: %s", story_url)
                    story_result = create_container(story_url, "", is_story=True)
                    if story_result.get("success") and story_result.get("creation_id"):
                        story_pub = publish_media(story_result["creation_id"])
                        if story_pub.get("success"):
                            ad_obj.instagram_story_id = story_pub.get("id", "")
                            ad_obj.is_instagram_published = True
                            ad_obj.save(update_fields=["instagram_story_id", "is_instagram_published"])
                            logger.info("post_manager.distribute_ad: Instagram Story published for ad %s", ad_obj.uuid)
                        else:
                            logger.warning("post_manager.distribute_ad: Instagram Story publish failed: %s", story_pub.get("message"))
                    else:
                        logger.warning("post_manager.distribute_ad: Instagram Story container failed: %s", story_result.get("message"))
            except Exception as exc:
                logger.exception("post_manager.distribute_ad: Instagram delivery crashed ad=%s: %s", ad_obj.uuid, exc)

    return telegram_ok or instagram_ok
