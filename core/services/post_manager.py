"""
Iraniu — Distribute approved ads: generate image, post to Telegram and Instagram.
Telegram: uses Site Configuration default channel (telegram_channel_id + default_telegram_bot) when
is_channel_active; otherwise falls back to TelegramChannel (is_default, current environment).
Uses AdTemplate (is_active=True) for image generation and Instagram Graph API for Feed + Story.

NOTE: For automatic distribution on approval, see DeliveryService in core/services/delivery.py.
This module's distribute_ad() is kept for the manual "Preview & Publish" flow.
"""

import logging
from typing import Optional

from django.conf import settings

from core.models import AdRequest, AdTemplate, SiteConfiguration, TelegramChannel, FORMAT_STORY
from core.notifications import send_notification
from core.services.telegram_client import send_message, send_photo
from core.services.image_engine import create_ad_image, make_story_image
from core.services.instagram_client import create_container, publish_media
from core.services.instagram_api import _path_to_public_url

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
    1. Generate ad image from active AdTemplate (create_ad_image).
    2. Upload to Telegram (send_photo with image URL, or send_message if no image).
    3. Upload as Instagram Post (create_container + publish_media).
    4. Upload as Instagram Story 9:16 (create_ad_image with format_type='STORY').

    Returns True if at least Telegram send succeeded (or image generation + Instagram succeeded), False otherwise.
    """
    if not isinstance(ad_obj, AdRequest):
        logger.warning("post_manager.distribute_ad: invalid ad type")
        return False
    if ad_obj.status != AdRequest.Status.APPROVED:
        logger.debug("post_manager.distribute_ad: ad %s not approved, status=%s", ad_obj.uuid, ad_obj.status)
        return False

    # Use Persian category name (name_fa) with fallback
    category_fa = ""
    if ad_obj.category:
        category_fa = getattr(ad_obj.category, 'name_fa', '') or ad_obj.category.name
    if not category_fa:
        category_fa = ad_obj.get_category_display() if hasattr(ad_obj, "get_category_display") else "Other"
    text = (ad_obj.content or "").strip()
    contact = getattr(ad_obj, "contact_snapshot", None) or {}
    phone = (contact.get("phone") or "").strip() if isinstance(contact, dict) else ""
    if not phone and getattr(ad_obj, "user_id", None) and ad_obj.user:
        phone = (ad_obj.user.phone_number or "").strip()
    caption = f"{category_fa}\n\n{text}"
    if phone:
        caption += f"\n\n📱 {phone}"

    template = AdTemplate.objects.filter(is_active=True).first()
    feed_path = None
    if template:
        try:
            feed_path = create_ad_image(template.pk, category_fa, text, phone)
        except Exception as exc:
            logger.exception("post_manager.distribute_ad: image generation failed ad=%s: %s", ad_obj.uuid, exc)
    feed_url = _path_to_public_url(feed_path) if feed_path else None

    # ──────────────────────────────────────────────
    # Telegram Channel
    # ──────────────────────────────────────────────
    chat_id, token, _ = _channel_from_site_config()
    if token is None or chat_id is None:
        channel = get_default_channel()
        if channel:
            try:
                token = channel.bot_connection.get_decrypted_token()
                chat_id = int(channel.channel_id.strip())
            except Exception as e:
                logger.warning("post_manager.distribute_ad: channel %s: %s", getattr(channel, "title", ""), e)
    if not token or chat_id is None:
        fallback_id = (getattr(settings, "TELEGRAM_DEFAULT_CHANNEL_ID", None) or "").strip()
        if fallback_id:
            from core.models import TelegramBot
            env = getattr(settings, "ENVIRONMENT", "PROD")
            default_bot = (
                TelegramBot.objects.filter(environment=env, is_active=True)
                .order_by("-is_default")
                .first()
            )
            if default_bot:
                try:
                    token = default_bot.get_decrypted_token()
                    chat_id = int(fallback_id)
                except (ValueError, TypeError, Exception):
                    pass
    telegram_ok = False
    if token and chat_id is not None:
        try:
            if feed_url:
                telegram_ok, _, _ = send_photo(token, chat_id, feed_url, caption=caption[:1024])
            else:
                telegram_ok, _, _ = send_message(token, chat_id, caption[:4096])
            if not telegram_ok:
                logger.warning("post_manager.distribute_ad: Telegram send failed for ad %s", ad_obj.uuid)
                send_notification(
                    "error",
                    "Telegram post failed for ad. Check bot token and channel permissions.",
                    link="/settings/hub/telegram/",
                )
        except Exception as exc:
            logger.exception("post_manager.distribute_ad: Telegram delivery crashed ad=%s: %s", ad_obj.uuid, exc)
            send_notification(
                "error",
                f"Telegram delivery error: {exc!s}. Check bot and channel.",
                link="/settings/hub/telegram/",
            )

    # ──────────────────────────────────────────────
    # Instagram Feed + Story
    # ──────────────────────────────────────────────
    instagram_ok = False
    config = SiteConfiguration.get_config()
    if not getattr(config, 'is_instagram_enabled', False):
        logger.info("post_manager.distribute_ad: Instagram disabled, skipping for ad %s", ad_obj.uuid)
    elif feed_url:
        try:
            # Feed post
            result = create_container(feed_url, caption[:2200], is_story=False)
            if result.get("success") and result.get("creation_id"):
                pub = publish_media(result["creation_id"])
                if pub.get("success"):
                    instagram_ok = True
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

            # Story — use format_type='STORY' (auto Y+285 offset) for proper 9:16 rendering
            story_path = None
            if template:
                try:
                    story_path = create_ad_image(
                        template.pk, category_fa, text, phone,
                        format_type=FORMAT_STORY,
                    )
                except Exception as exc:
                    logger.warning("post_manager.distribute_ad: story image gen failed ad=%s: %s", ad_obj.uuid, exc)
                    # Fallback: old make_story_image approach
                    story_path = make_story_image(feed_path) if feed_path else None

            story_url = _path_to_public_url(story_path) if story_path else None
            if story_url:
                story_result = create_container(story_url, "", is_story=True)
                if story_result.get("success") and story_result.get("creation_id"):
                    story_pub = publish_media(story_result["creation_id"])
                    if story_pub.get("success"):
                        logger.info("post_manager.distribute_ad: Instagram Story published for ad %s", ad_obj.uuid)
                    else:
                        logger.warning("post_manager.distribute_ad: Instagram Story publish failed: %s", story_pub.get("message"))
                else:
                    logger.warning("post_manager.distribute_ad: Instagram Story container failed: %s", story_result.get("message"))
        except Exception as exc:
            logger.exception("post_manager.distribute_ad: Instagram delivery crashed ad=%s: %s", ad_obj.uuid, exc)

    return telegram_ok or instagram_ok
