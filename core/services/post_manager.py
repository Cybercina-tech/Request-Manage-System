"""
Iraniu — Distribute approved ads: Telegram via DeliveryService (professional caption), Instagram via queue or direct.
Telegram is always sent through DeliveryService.send(ad, 'telegram_channel') so only the professional
caption (🚀 #آگهی_جدید) is used. Single-execution guard is in DeliveryService.

Instagram delivery runs in a background thread when queue is OFF to avoid blocking the UI (30+ second API round-trip).

NOTE: For automatic distribution on approval, see DeliveryService in core/services/delivery.py.
This module's distribute_ad() is for the manual "Preview & Publish" button; it delegates Telegram to DeliveryService.
"""

import logging
import threading
from typing import Optional, Tuple

from django.conf import settings

from core.models import AdRequest, DeliveryLog, InstagramSettings, SiteConfiguration, TelegramChannel
from core.notifications import send_notification
from core.services.image_engine import ensure_feed_image
from core.services.delivery import DeliveryService

logger = logging.getLogger(__name__)


def _run_instagram_delivery_background(ad_pk: int) -> None:
    """
    Background worker: send ad to Instagram Feed + Story via DeliveryService.
    Runs in a separate thread so the UI returns immediately. Closes DB connection when done.
    """
    import django
    django.setup()

    from django.db import connection

    try:
        ad = AdRequest.objects.select_related('category', 'user', 'bot').get(pk=ad_pk)
    except AdRequest.DoesNotExist:
        logger.error("_run_instagram_delivery_background: ad pk=%s not found", ad_pk)
        return

    feed_ok = False
    story_ok = False
    try:
        feed_ok = DeliveryService.send(ad, 'instagram', force_deliver=True)
        story_ok = DeliveryService.send(ad, 'instagram_story', force_deliver=True)
    except Exception as exc:
        logger.exception("_run_instagram_delivery_background: crash ad=%s: %s", ad.uuid, exc)
        try:
            send_notification(
                "error",
                "Instagram delivery crashed. Check System Logs.",
                link=f"/requests/{ad.uuid}/",
            )
        except Exception:
            pass
    else:
        if feed_ok or story_ok:
            try:
                send_notification(
                    "success",
                    f"آگهی {str(ad.uuid)[:8]} با موفقیت در اینستاگرام منتشر شد.",
                    link=f"/requests/{ad.uuid}/",
                )
            except Exception:
                pass
        else:
            try:
                send_notification(
                    "error",
                    "Instagram post or story failed. Check token and that image URL is public.",
                    link="/settings/hub/instagram/",
                )
            except Exception:
                pass
    finally:
        connection.close()


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


def distribute_ad(ad_obj: AdRequest) -> Tuple[bool, bool]:
    """
    Manual distribution (from Preview & Publish page):
    1. Telegram: delegated to DeliveryService.send(ad, 'telegram_channel') — professional caption only, single-execution guard there.
    2. Generate Feed image for Instagram if needed.
    3. Instagram Feed + Story: queue, or run in background thread when queue is OFF (non-blocking).

    Returns (ok, instagram_in_background):
      - ok: True if at least Telegram succeeded or Instagram was queued/started.
      - instagram_in_background: True when Instagram is being processed in a background thread (UI should show "در صف انتشار" message).
    """
    if not isinstance(ad_obj, AdRequest):
        logger.warning("post_manager.distribute_ad: invalid ad type")
        return False, False
    if ad_obj.status != AdRequest.Status.APPROVED:
        logger.debug("post_manager.distribute_ad: ad %s not approved, status=%s", ad_obj.uuid, ad_obj.status)
        return False, False

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
    # When OFF: run in background thread so UI returns immediately.
    # ──────────────────────────────────────────────
    instagram_ok = False
    instagram_in_background = False
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
            # Run Instagram delivery in background thread — do not block UI (30+ second API round-trip)
            thread = threading.Thread(
                target=_run_instagram_delivery_background,
                args=(ad_obj.pk,),
                name=f"instagram-distribute-{ad_obj.uuid}",
                daemon=True,
            )
            thread.start()
            logger.info("post_manager.distribute_ad: Instagram delivery started in background for ad %s", ad_obj.uuid)
            instagram_ok = True
            instagram_in_background = True

    return (telegram_ok or instagram_ok, instagram_in_background)
