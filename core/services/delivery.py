"""
Iraniu — Unified delivery layer. Routes approved ads to all platforms:
  1. Telegram DM (user approval notification)
  2. Telegram Channel (ad post with generated image)
  3. Instagram Feed (post via Graph API)
  4. Instagram Story (9:16 story via Graph API)
  5. API (passive — partners fetch via /api/v1/list/)

Centralized error handling and DeliveryLog. Business logic only; no request objects.
Each channel is independent: failure in one does NOT block others.
"""

import logging
from django.utils import timezone

from core.models import (
    AdRequest,
    AdTemplate,
    SiteConfiguration,
    DeliveryLog,
)

logger = logging.getLogger(__name__)


class DeliveryService:
    """
    Send approved ad to a single channel. Validates ad status, routes to channel sender,
    creates/updates DeliveryLog. Use send(ad, channel) for one channel; callers may
    loop over channels for multi-channel delivery.
    """

    # All channels in delivery order: user notification first, then public distribution
    SUPPORTED_CHANNELS = (
        'telegram',           # DM to user
        'telegram_channel',   # Post to Telegram channel
        'instagram',          # Instagram Feed post
        'instagram_story',    # Instagram Story (9:16)
        'api',                # Passive API availability
    )

    # Channels that involve external API calls (for threading decisions)
    SLOW_CHANNELS = ('telegram_channel', 'instagram', 'instagram_story')

    @staticmethod
    def send(ad: AdRequest, channel: str) -> bool:
        """
        Deliver ad to the given channel. Returns True if delivery succeeded.
        Validates ad is approved; creates DeliveryLog (pending -> success/failed).
        """
        if not isinstance(ad, AdRequest):
            logger.warning("DeliveryService.send: invalid ad type")
            return False
        if channel not in DeliveryService.SUPPORTED_CHANNELS:
            logger.warning("DeliveryService.send: unsupported channel=%s", channel)
            return False
        if ad.status != AdRequest.Status.APPROVED:
            logger.info("DeliveryService.send: ad %s not approved, status=%s", ad.uuid, ad.status)
            return False

        log = DeliveryLog.objects.create(
            ad=ad,
            channel=channel,
            status=DeliveryLog.DeliveryStatus.PENDING,
        )
        try:
            if channel == 'telegram':
                ok = DeliveryService._send_telegram(ad)
            elif channel == 'telegram_channel':
                ok = DeliveryService._send_telegram_channel(ad, log)
            elif channel in ('instagram', 'instagram_story'):
                # Skip Instagram delivery if not enabled in SiteConfiguration
                from core.models import SiteConfiguration
                config = SiteConfiguration.get_config()
                if not getattr(config, 'is_instagram_enabled', False):
                    logger.info("DeliveryService.send: Instagram disabled, skipping channel=%s for ad %s", channel, ad.uuid)
                    log.status = DeliveryLog.DeliveryStatus.FAILED
                    log.error_message = 'Instagram is not enabled (incomplete configuration)'
                    log.save(update_fields=['status', 'error_message'])
                    return False
                if channel == 'instagram':
                    ok = DeliveryService._send_instagram(ad, log)
                else:
                    ok = DeliveryService._send_instagram_story(ad, log)
            else:  # api
                ok = DeliveryService._send_api(ad)

            log.status = DeliveryLog.DeliveryStatus.SUCCESS if ok else DeliveryLog.DeliveryStatus.FAILED
            if not ok and log.error_message == '':
                log.error_message = 'Delivery returned failure'
            log.response_payload = {'success': ok}
            log.save(update_fields=['status', 'error_message', 'response_payload'])
            return ok
        except Exception as e:
            logger.exception("DeliveryService.send channel=%s ad=%s: %s", channel, ad.uuid, e)
            log.status = DeliveryLog.DeliveryStatus.FAILED
            log.error_message = str(e)[:500]
            log.response_payload = {}
            log.save(update_fields=['status', 'error_message', 'response_payload'])
            return False

    # ------------------------------------------------------------------
    # Channel: Telegram DM (user approval notification)
    # ------------------------------------------------------------------

    @staticmethod
    def _send_telegram(ad: AdRequest) -> bool:
        """Send approval notification via Telegram DM (localized, category, no Ad ID)."""
        from core.i18n import get_message, get_category_display_name
        from core.models import TelegramSession
        from core.services import send_telegram_message, send_telegram_message_via_bot

        config = SiteConfiguration.get_config()
        lang = "en"
        if ad.telegram_user_id and ad.bot_id:
            session = TelegramSession.objects.filter(
                telegram_user_id=ad.telegram_user_id, bot_id=ad.bot_id
            ).first()
            if session and session.language:
                lang = session.language
        category_name = (ad.category.name if ad.category else get_category_display_name("other", lang))
        msg = get_message("notification_approved", lang).format(category=category_name)
        if not ad.telegram_user_id:
            logger.debug("DeliveryService._send_telegram: no telegram_user_id for ad %s", ad.uuid)
            return True  # nothing to send, consider success
        if ad.bot and ad.bot.is_active:
            return send_telegram_message_via_bot(ad.telegram_user_id, msg, ad.bot)
        return send_telegram_message(ad.telegram_user_id, msg, config)

    # ------------------------------------------------------------------
    # Channel: Telegram Channel (public ad post with generated image)
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_channel_id(raw_id: str) -> int | None:
        """
        Validate and normalise a Telegram channel/supergroup ID.
        Channels & supergroups must start with -100.  If the stored value
        is a bare numeric ID (e.g. 123456789), prepend -100 automatically.
        Returns an int chat_id or None on failure.
        """
        raw = (raw_id or "").strip()
        if not raw:
            return None
        try:
            val = int(raw)
        except (ValueError, TypeError):
            return None
        s = str(val)
        if s.startswith("-100"):
            return val
        # Bare positive ID → prepend -100
        if val > 0:
            corrected = int(f"-100{val}")
            logger.info(
                "_normalize_channel_id: bare ID %s auto-corrected to %s",
                val, corrected,
            )
            return corrected
        # Negative but not -100… (e.g. old-style group -12345)
        return val

    @staticmethod
    def _get_ad_phone(ad: AdRequest) -> str:
        """Extract phone number from ad contact_snapshot or user profile."""
        contact = getattr(ad, "contact_snapshot", None) or {}
        phone = (contact.get("phone") or "").strip() if isinstance(contact, dict) else ""
        if not phone and getattr(ad, "user_id", None) and ad.user:
            phone = (ad.user.phone_number or "").strip()
        return phone

    @staticmethod
    def _build_channel_caption(ad: AdRequest) -> str:
        """
        Build a professional Persian HTML caption for the Telegram channel post.

        Layout:
          🚀 <b>#آگهی_جدید</b>
                                          ← blank line
          🔹 <b>متن آگهی:</b> {desc} | 📞 <b>تماس:</b> {phone}
                                          ← blank line
          📂 دسته‌بندی: #{category}
          ━━━━━━━━━━━━━━━━
          🆔 @Channel
          📥 ثبت آگهی: @Bot

        Phone number is included inline with the description.
        No inline keyboard — Telegram rejects tel: URLs in buttons.
        Respects the 1024-char photo-caption limit.
        """
        # ---- Category → hashtag ----
        category_fa = ""
        if ad.category:
            category_fa = getattr(ad.category, "name_fa", "") or ad.category.name
        if not category_fa:
            category_fa = (
                ad.get_category_display()
                if hasattr(ad, "get_category_display")
                else "سایر"
            )
        # Convert to hashtag: replace spaces with underscore
        category_hashtag = "#" + category_fa.replace(" ", "_")

        # ---- Description (truncate to 600 chars to leave room for phone + footer) ----
        description = (ad.content or "").strip()
        if len(description) > 600:
            description = description[:600].rsplit(" ", 1)[0] + "…"

        # ---- Phone ----
        phone = DeliveryService._get_ad_phone(ad)

        # ---- Channel handle & bot username (from SiteConfiguration) ----
        config = SiteConfiguration.get_config()
        channel_handle = (getattr(config, "telegram_channel_handle", "") or "").strip()
        bot_username = (getattr(config, "telegram_bot_username", "") or "").strip()
        if not channel_handle:
            channel_handle = "@iraniu_bot"
        if not bot_username:
            bot_username = "iraniu_bot"
        # Ensure @ prefix for display
        bot_display = bot_username if bot_username.startswith("@") else f"@{bot_username}"

        # ---- Assemble HTML caption ----
        lines = [
            "🚀 <b>#آگهی_جدید</b>",
            "",
        ]

        # Description + phone combined on one line (pipe-separated)
        if description and phone:
            lines.append(f"🔹 <b>متن آگهی:</b> {description} | 📞 <b>تماس:</b> {phone}")
        elif description:
            lines.append(f"🔹 <b>متن آگهی:</b> {description}")
        elif phone:
            lines.append(f"📞 <b>تماس:</b> {phone}")

        lines.append("")
        lines.append(f"📂 دسته‌بندی: {category_hashtag}")
        lines.append("━━━━━━━━━━━━━━━━")
        lines.append(f"🆔 {channel_handle}")
        lines.append(f"📥 ثبت آگهی: {bot_display}")

        caption = "\n".join(lines)
        # Telegram photo caption hard-limit: 1024 chars
        if len(caption) > 1024:
            caption = caption[:1021] + "…"
        return caption

    @staticmethod
    def _send_telegram_channel(ad: AdRequest, log: DeliveryLog) -> bool:
        """
        Generate ad image and post to the default Telegram channel.
        Uses local file upload (multipart) instead of URL for reliability.
        Caption is HTML-formatted, Persian, professional.
        """
        from core.services.telegram_client import send_message, send_photo
        from core.services.image_engine import generate_ad_image
        from core.models import TelegramBot, TelegramChannel as TelegramChannelModel

        config = SiteConfiguration.get_config()

        # ---- Build caption ----
        caption = DeliveryService._build_channel_caption(ad)

        # ---- Generate image (local file path) ----
        feed_path = None
        try:
            feed_path = generate_ad_image(ad, is_story=False)
        except Exception as exc:
            logger.warning("_send_telegram_channel: image gen failed ad=%s: %s", ad.uuid, exc)

        # ---- Resolve channel & bot token ----
        chat_id, token = None, None

        if (
            config.is_channel_active
            and (config.telegram_channel_id or "").strip()
            and config.default_telegram_bot
        ):
            bot = config.default_telegram_bot
            if bot and bot.is_active:
                try:
                    token = bot.get_decrypted_token()
                    chat_id = DeliveryService._normalize_channel_id(
                        config.telegram_channel_id
                    )
                except Exception:
                    pass

        if not token or chat_id is None:
            from django.conf import settings as django_settings

            env = getattr(django_settings, "ENVIRONMENT", "PROD")
            channel_obj = (
                TelegramChannelModel.objects.filter(
                    is_default=True,
                    is_active=True,
                    bot_connection__environment=env,
                )
                .select_related("bot_connection")
                .first()
            )
            if channel_obj:
                try:
                    token = channel_obj.bot_connection.get_decrypted_token()
                    chat_id = DeliveryService._normalize_channel_id(
                        channel_obj.channel_id
                    )
                except Exception as e:
                    logger.warning(
                        "_send_telegram_channel: channel resolution failed: %s", e
                    )

        if not token or chat_id is None:
            log.error_message = "No active Telegram channel configured."
            logger.info("_send_telegram_channel: no channel for ad %s", ad.uuid)
            return False

        # ---- Send photo (local file) or text-only ----
        try:
            if feed_path:
                import os

                if not os.path.isfile(feed_path):
                    log.error_message = f"Generated image not found: {feed_path}"
                    logger.warning(
                        "_send_telegram_channel: file missing %s ad=%s",
                        feed_path, ad.uuid,
                    )
                    return False

                ok, msg_id, err = send_photo(
                    token,
                    chat_id,
                    feed_path,
                    caption=caption,
                    parse_mode="HTML",
                    max_retries=3,
                )
            else:
                ok, msg_id, err = send_message(
                    token, chat_id, caption[:4096],
                )

            if not ok:
                log.error_message = (err or "Telegram send returned failure.")[:500]
                logger.warning(
                    "_send_telegram_channel: send failed ad=%s err=%s",
                    ad.uuid, err,
                )
            return ok
        except Exception as exc:
            log.error_message = str(exc)[:500]
            logger.exception(
                "_send_telegram_channel: crash ad=%s: %s", ad.uuid, exc
            )
            return False

    # ------------------------------------------------------------------
    # Channel: Instagram Feed (post via Graph API)
    # ------------------------------------------------------------------

    @staticmethod
    def _send_instagram(ad: AdRequest, log: DeliveryLog | None = None) -> bool:
        """Post ad to Instagram Feed via InstagramService. Populates log.error_message on failure."""
        from core.services.instagram import InstagramService

        result = InstagramService.post_ad(ad)
        if isinstance(result, dict) and result.get('success'):
            if log:
                log.response_payload = {'media_id': result.get('id', '')}
            return True
        if log is not None and isinstance(result, dict) and result.get('message'):
            log.error_message = result.get('message', '')[:500]
        return False

    # ------------------------------------------------------------------
    # Channel: Instagram Story (9:16 via Graph API)
    # ------------------------------------------------------------------

    @staticmethod
    def _send_instagram_story(ad: AdRequest, log: DeliveryLog) -> bool:
        """
        Generate a 9:16 story image from the ad and post as Instagram Story.
        Uses the image engine with format_type='STORY', then Graph API with media_type='STORIES'.
        """
        from core.services.image_engine import generate_ad_image
        from core.services.instagram_api import _path_to_public_url
        from core.services.instagram_client import create_container, publish_media

        # Generate story image using the high-level Ad-based API
        try:
            story_path = generate_ad_image(ad, is_story=True)
        except Exception as exc:
            log.error_message = f"Story image generation failed: {exc!s}"[:500]
            logger.warning("_send_instagram_story: image gen failed ad=%s: %s", ad.uuid, exc)
            return False

        if not story_path:
            log.error_message = "Story image generation returned None."
            return False

        story_url = _path_to_public_url(story_path)
        if not story_url:
            log.error_message = "Could not resolve story image to public URL."
            return False

        # Post as Story (no caption — Graph API doesn't support captions on Stories)
        try:
            container = create_container(story_url, "", is_story=True)
            if not container.get("success") or not container.get("creation_id"):
                msg = container.get("message", "Container creation failed")
                log.error_message = msg[:500]
                logger.warning("_send_instagram_story: container failed ad=%s: %s", ad.uuid, msg)
                return False

            pub = publish_media(container["creation_id"])
            if pub.get("success"):
                log.response_payload = {'media_id': pub.get('id', '')}
                logger.info("_send_instagram_story: published for ad %s", ad.uuid)
                return True
            else:
                msg = pub.get("message", "Publish failed")
                log.error_message = msg[:500]
                logger.warning("_send_instagram_story: publish failed ad=%s: %s", ad.uuid, msg)
                return False
        except Exception as exc:
            log.error_message = str(exc)[:500]
            logger.exception("_send_instagram_story: crash ad=%s: %s", ad.uuid, exc)
            return False

    # ------------------------------------------------------------------
    # Channel: API (passive)
    # ------------------------------------------------------------------

    @staticmethod
    def _send_api(ad: AdRequest) -> bool:
        """
        Record ad as delivered to API channel (partners can fetch via /api/v1/list/).
        No outbound HTTP; availability via API is the delivery.
        """
        return True
