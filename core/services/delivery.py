"""
Iraniu — Unified delivery layer. Routes approved ads to all platforms:
  1. Telegram DM (user approval notification)
  2. Telegram Channel (ad post with generated image)
  3. Instagram Feed (post via Graph API)
  4. Instagram Story (9:16 story via Graph API)
  5. API (passive — partners fetch via /api/v1/list/)

Centralized error handling, DeliveryLog, and SystemLog. Business logic only; no request objects.
Each channel is independent: failure in one does NOT block others.
"""

import logging
import time
from django.utils import timezone

from core.models import (
    AdRequest,
    AdTemplate,
    DeliveryLog,
    InstagramSettings,
    SiteConfiguration,
    SystemLog,
)
from core.services.log_service import (
    log_event,
    log_exception,
    parse_facebook_error,
    parse_telegram_error,
)

logger = logging.getLogger(__name__)
_log_bot = logging.getLogger('core.instagram.bot')


def _log_instagram_bot(status: str, target: str, ad_uuid, detail: str = ''):
    """Write one line to bot_log.txt for Instagram post/story success or failure."""
    _log_bot.info('Instagram %s %s ad=%s %s', status, target, ad_uuid, detail)


def _channel_to_category(channel: str) -> str:
    """Map delivery channel to SystemLog category."""
    m = {
        'telegram': 'TELEGRAM_BOT',
        'telegram_channel': 'TELEGRAM_BOT',
        'instagram': 'INSTAGRAM_API',
        'instagram_story': 'INSTAGRAM_API',
        'webhook': 'WEBHOOK',
        'api': 'SYSTEM_CORE',
    }
    return m.get(channel, 'SYSTEM_CORE')


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
        'webhook',            # POST JSON to external URL (when enable_webhook_sync + external_webhook_url)
    )

    # Channels that involve external API calls (for threading decisions)
    SLOW_CHANNELS = ('telegram_channel', 'instagram', 'instagram_story', 'webhook')

    @staticmethod
    def send(ad: AdRequest, channel: str, force_deliver: bool = False) -> bool:
        """
        Deliver ad to the given channel. Returns True if delivery succeeded.
        Validates ad is approved; creates DeliveryLog (pending -> success/failed).
        When force_deliver=True (e.g. from queue processor), skip Instagram queue check and send immediately.
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

        # Webhook: skip if disabled or no URL (no log created)
        if channel == 'webhook':
            config = SiteConfiguration.get_config()
            url = (getattr(config, 'external_webhook_url', None) or '').strip()
            if not getattr(config, 'enable_webhook_sync', False) or not url:
                return True

        # Telegram channel: post only once per ad (single execution guard)
        if channel == 'telegram_channel':
            if DeliveryLog.objects.filter(
                ad=ad,
                channel=DeliveryLog.Channel.TELEGRAM_CHANNEL,
                status=DeliveryLog.DeliveryStatus.SUCCESS,
            ).exists():
                logger.info("DeliveryService.send: ad %s already delivered to Telegram channel, skipping", ad.uuid)
                return True

        # If this ad/channel was already queued, reuse the existing log; otherwise create one
        log = None
        if force_deliver and channel in ('instagram', 'instagram_story'):
            log = DeliveryLog.objects.filter(
                ad=ad, channel=channel, status=DeliveryLog.DeliveryStatus.QUEUED
            ).first()
        if log is None:
            log = DeliveryLog.objects.create(
                ad=ad,
                channel=channel,
                status=DeliveryLog.DeliveryStatus.PENDING,
            )
        else:
            log.status = DeliveryLog.DeliveryStatus.PENDING
            log.save(update_fields=['status'])
        try:
            if channel == 'telegram':
                ok = DeliveryService._send_telegram(ad)
            elif channel == 'telegram_channel':
                ok = DeliveryService._send_telegram_channel(ad, log)
            elif channel in ('instagram', 'instagram_story'):
                # Skip Instagram delivery if not enabled in SiteConfiguration
                config = SiteConfiguration.get_config()
                if not getattr(config, 'is_instagram_enabled', False):
                    logger.info("DeliveryService.send: Instagram disabled, skipping channel=%s for ad %s", channel, ad.uuid)
                    log.status = DeliveryLog.DeliveryStatus.FAILED
                    log.error_message = 'Instagram is not enabled (incomplete configuration)'
                    log.save(update_fields=['status', 'error_message'])
                    return False
                # If Instagram queue is ON and we're not force-delivering: do not send now; mark as QUEUED
                if not force_deliver:
                    ig_settings = InstagramSettings.get_settings()
                    if getattr(ig_settings, 'enable_instagram_queue', False):
                        log.status = DeliveryLog.DeliveryStatus.QUEUED
                        log.save(update_fields=['status'])
                        ad.instagram_queue_status = 'queued'
                        ad.save(update_fields=['instagram_queue_status'])
                        _log_instagram_bot('QUEUED', 'Feed' if channel == 'instagram' else 'Story', ad.uuid, 'added to queue')
                        return True
                if channel == 'instagram':
                    ok = DeliveryService._send_instagram(ad, log)
                else:
                    ok = DeliveryService._send_instagram_story(ad, log)
            elif channel == 'webhook':
                ok = DeliveryService._send_webhook(ad, log)
            else:  # api
                ok = DeliveryService._send_api(ad)

            log.status = DeliveryLog.DeliveryStatus.SUCCESS if ok else DeliveryLog.DeliveryStatus.FAILED
            if not ok and log.error_message == '':
                log.error_message = 'Delivery returned failure'
            log.response_payload = (log.response_payload or {}) | {'success': ok}
            log.save(update_fields=['status', 'error_message', 'response_payload'])
            cat = _channel_to_category(channel)
            if ok:
                log_event(SystemLog.Level.INFO, cat, f"Delivery success {channel} ad={ad.uuid}", ad_request=ad, status_code=200, response_data=log.response_payload)
            else:
                meta = {}
                if channel in ('instagram', 'instagram_story') and log.response_payload:
                    fb_err = parse_facebook_error(log.response_payload)
                    if fb_err.get('fb_trace_id'):
                        meta['fb_trace_id'] = fb_err['fb_trace_id']
                log_event(SystemLog.Level.ERROR, cat, f"Delivery failed {channel}: {log.error_message[:200]}", ad_request=ad, status_code=500, request_data={'channel': channel}, response_data=log.response_payload, metadata=meta or None)
            return ok
        except Exception as e:
            logger.exception("DeliveryService.send channel=%s ad=%s: %s", channel, ad.uuid, e)
            log.status = DeliveryLog.DeliveryStatus.FAILED
            log.error_message = str(e)[:500]
            log.response_payload = {}
            log.save(update_fields=['status', 'error_message', 'response_payload'])
            log_exception(e, _channel_to_category(channel), f"Delivery failed {channel}: {str(e)[:200]}", ad_request=ad)
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

        # ---- Channel handle & bot username for caption (use @iraniu_bot in caption) ----
        config = SiteConfiguration.get_config()
        channel_handle = (getattr(config, "telegram_channel_handle", "") or "").strip()
        if not channel_handle:
            channel_handle = "@iraniu_bot"
        # Telegram caption always shows @iraniu_bot (not @Iraniu_ads_bot)
        bot_display = "@iraniu_bot"

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
                err_str = (err or "Telegram send returned failure.")[:500]
                log.error_message = err_str
                log.response_payload = {'ok': False, 'description': err_str}
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
        """
        Post ad to Instagram Feed. Uses ad.generated_image (generated if missing).
        Public URL must be absolute (e.g. https://request.iraniu.uk/media/...) so Meta can fetch it.
        Validates token before upload; waits for container FINISHED before publishing.
        """
        from core.services.image_engine import ensure_feed_image
        from core.services.instagram_api import get_absolute_media_url, is_public_media_url
        from core.services.instagram_client import (
            _get_credentials,
            create_container,
            publish_media,
            wait_for_container_ready,
        )
        from core.services.instagram import InstagramService, validate_instagram_token

        # Token valid? Check before starting upload
        _, token = _get_credentials()
        if not token:
            if log:
                log.error_message = 'Instagram not configured (no access token).'
            _log_instagram_bot('FAILED', 'Feed', ad.uuid, 'no token')
            return False
        valid, msg = validate_instagram_token(token)
        if not valid:
            logger.warning("Token Expired or invalid before Feed upload: %s", msg)
            if log:
                log.error_message = f'Token Expired: {(msg or "invalid token")[:200]}'
            _log_instagram_bot('FAILED', 'Feed', ad.uuid, 'Token Expired')
            return False

        if not ensure_feed_image(ad):
            if log:
                log.error_message = 'Feed image generation or save failed.'
            _log_instagram_bot('FAILED', 'Feed', ad.uuid, 'image generation or save failed')
            return False
        image_url = get_absolute_media_url(ad.generated_image)
        if not image_url or not image_url.startswith('http'):
            if log:
                log.error_message = 'Feed image URL not public (set production_base_url or INSTAGRAM_BASE_URL).'
            _log_instagram_bot('FAILED', 'Feed', ad.uuid, 'image URL not public')
            return False
        if not is_public_media_url(image_url):
            if log:
                log.error_message = (
                    'Feed image URL must be public HTTPS (no localhost or private IP). '
                    'Set production_base_url to your public domain.'
                )
            _log_instagram_bot('FAILED', 'Feed', ad.uuid, 'image URL not public (localhost/private IP)')
            return False
        logger.info('Sending Feed URL to Instagram: %s', image_url)
        caption = InstagramService.format_caption(ad, lang='fa')
        result = create_container(image_url, caption[:2200], is_story=False)
        if not result.get('success') or not result.get('creation_id'):
            msg = result.get('message', 'Container creation failed')[:500]
            if log:
                log.error_message = msg
                log.response_payload = {'error_data': result.get('error_data'), 'http_status': result.get('http_status'), 'raw_response': result.get('raw_response')}
            _log_instagram_bot('FAILED', 'Feed', ad.uuid, msg)
            return False
        creation_id = result['creation_id']
        # 30s delay so Instagram can fetch and process the image before we check status
        time.sleep(30)
        ready, status_msg = wait_for_container_ready(creation_id)
        if not ready:
            if log:
                log.error_message = status_msg or 'Container did not reach FINISHED'
            _log_instagram_bot('FAILED', 'Feed', ad.uuid, status_msg or 'container not FINISHED')
            return False
        pub = publish_media(creation_id)
        if not pub.get('success'):
            msg = pub.get('message', 'Publish failed')[:500]
            if log:
                log.error_message = msg
                log.response_payload = {'error_data': pub.get('error_data'), 'http_status': pub.get('http_status'), 'raw_response': pub.get('raw_response')}
            _log_instagram_bot('FAILED', 'Feed', ad.uuid, msg)
            return False
        media_id = pub.get('id', '')
        if log:
            log.response_payload = {'media_id': media_id}
        ad.instagram_post_id = media_id
        ad.is_instagram_published = True
        ad.save(update_fields=['instagram_post_id', 'is_instagram_published'])
        _log_instagram_bot('SUCCESS', 'Feed', ad.uuid, f'media_id={media_id}')
        return True

    # ------------------------------------------------------------------
    # Channel: Instagram Story (9:16 via Graph API)
    # ------------------------------------------------------------------

    @staticmethod
    def _send_instagram_story(ad: AdRequest, log: DeliveryLog) -> bool:
        """
        Post ad to Instagram Story (9:16). Uses ad.generated_story_image (generated if missing).
        Public URL must be absolute so Meta crawler can fetch it (no login).
        Validates token before upload; 30s delay then wait for container FINISHED before publishing.
        """
        from core.services.image_engine import ensure_story_image
        from core.services.instagram_api import get_absolute_media_url, get_instagram_base_url, is_public_media_url
        from core.services.instagram_client import (
            _get_credentials,
            create_container,
            publish_media,
            wait_for_container_ready,
        )
        from core.services.instagram import validate_instagram_token

        # Token valid? Check before starting upload
        _, token = _get_credentials()
        if not token:
            log.error_message = 'Instagram not configured (no access token).'
            _log_instagram_bot('FAILED', 'Story', ad.uuid, 'no token')
            return False
        valid, msg = validate_instagram_token(token)
        if not valid:
            logger.warning("Token Expired or invalid before Story upload: %s", msg)
            log.error_message = f'Token Expired: {(msg or "invalid token")[:200]}'
            _log_instagram_bot('FAILED', 'Story', ad.uuid, 'Token Expired')
            return False

        if not ensure_story_image(ad):
            log.error_message = 'Story image generation or save failed.'
            _log_instagram_bot('FAILED', 'Story', ad.uuid, 'image generation or save failed')
            return False
        story_url = get_absolute_media_url(ad.generated_story_image)
        if not story_url or not story_url.startswith('http'):
            log.error_message = 'Story image URL not public (set production_base_url or INSTAGRAM_BASE_URL).'
            _log_instagram_bot('FAILED', 'Story', ad.uuid, 'image URL not public')
            return False
        if not is_public_media_url(story_url):
            log.error_message = (
                'Story image URL must be public HTTPS (no localhost or private IP). '
                'Set production_base_url to your public domain.'
            )
            _log_instagram_bot('FAILED', 'Story', ad.uuid, 'image URL not public (localhost/private IP)')
            return False
        # Require URL to be under our production media base so Instagram can fetch it
        expected_media_prefix = get_instagram_base_url().rstrip('/') + '/media/'
        if not story_url.startswith(expected_media_prefix):
            log.error_message = (
                f'Story image URL must be under {expected_media_prefix!r}. Got: {story_url[:80]}...'
            )
            _log_instagram_bot('FAILED', 'Story', ad.uuid, 'URL not under production media base')
            return False
        _log_bot.info(
            'Instagram Story URL sent to Meta (verify in browser): ad=%s url=%s',
            ad.uuid,
            story_url,
        )
        logger.info('Sending Story URL to Instagram: %s', story_url)
        try:
            container = create_container(story_url, "", is_story=True)
            if not container.get("success") or not container.get("creation_id"):
                msg = container.get("message", "Container creation failed")[:500]
                log.error_message = msg
                log.response_payload = {'error_data': container.get('error_data'), 'http_status': container.get('http_status'), 'raw_response': container.get('raw_response')}
                logger.warning("_send_instagram_story: container failed ad=%s: %s", ad.uuid, msg)
                _log_instagram_bot('FAILED', 'Story', ad.uuid, msg)
                return False
            creation_id = container["creation_id"]
            # 30s delay so Instagram can fetch and process the image before we check status
            time.sleep(30)
            ready, status_msg = wait_for_container_ready(creation_id)
            if not ready:
                log.error_message = status_msg or 'Container did not reach FINISHED'
                logger.warning("_send_instagram_story: container not ready ad=%s: %s", ad.uuid, status_msg)
                _log_instagram_bot('FAILED', 'Story', ad.uuid, status_msg or 'container not FINISHED')
                return False
            pub = publish_media(creation_id)
            if pub.get("success"):
                media_id = pub.get('id', '')
                log.response_payload = {'media_id': media_id}
                ad.instagram_story_id = media_id
                ad.is_instagram_published = True
                ad.save(update_fields=['instagram_story_id', 'is_instagram_published'])
                logger.info("_send_instagram_story: published for ad %s", ad.uuid)
                _log_instagram_bot('SUCCESS', 'Story', ad.uuid, f'media_id={media_id}')
                return True
            msg = pub.get("message", "Publish failed")[:500]
            log.error_message = msg
            log.response_payload = {'error_data': pub.get('error_data'), 'http_status': pub.get('http_status'), 'raw_response': pub.get('raw_response')}
            logger.warning("_send_instagram_story: publish failed ad=%s: %s", ad.uuid, msg)
            _log_instagram_bot('FAILED', 'Story', ad.uuid, msg)
            return False
        except Exception as exc:
            log.error_message = str(exc)[:500]
            logger.exception("_send_instagram_story: crash ad=%s: %s", ad.uuid, exc)
            _log_instagram_bot('FAILED', 'Story', ad.uuid, str(exc)[:200])
            return False

    # ------------------------------------------------------------------
    # Channel: External Webhook (POST JSON to configured URL)
    # ------------------------------------------------------------------

    @staticmethod
    def _send_webhook(ad: AdRequest, log: DeliveryLog) -> bool:
        """
        POST ad payload as JSON to external_webhook_url with X-Webhook-Secret header.
        Payload: id, category, message, image_url, story_url, created_at.
        """
        import requests
        from core.services.instagram_api import get_absolute_media_url

        config = SiteConfiguration.get_config()
        url = (getattr(config, 'external_webhook_url', None) or '').strip()
        if not url:
            log.error_message = 'External webhook URL not configured.'
            return False
        secret = (getattr(config, 'webhook_secret_key', None) or '').strip()
        image_url = get_absolute_media_url(ad.generated_image) if ad.generated_image else None
        story_url = get_absolute_media_url(ad.generated_story_image) if ad.generated_story_image else None
        category_name = ad.category.name if ad.category else 'Other'
        payload = {
            'id': ad.pk,
            'uuid': str(ad.uuid),
            'category': category_name,
            'message': (ad.content or '')[:10000],
            'image_url': image_url or '',
            'story_url': story_url or '',
            'created_at': ad.created_at.isoformat() if ad.created_at else None,
        }
        headers = {'Content-Type': 'application/json'}
        if secret:
            headers['X-Webhook-Secret'] = secret
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
            if resp.status_code >= 200 and resp.status_code < 300:
                log.response_payload = {'status_code': resp.status_code}
                return True
            log.error_message = f'HTTP {resp.status_code}: {resp.text[:500]}'
            log.response_payload = {'status_code': resp.status_code, 'body_preview': resp.text[:500]}
            return False
        except requests.RequestException as e:
            log.error_message = str(e)[:500]
            log.response_payload = {}
            return False
        except Exception as e:
            log.error_message = str(e)[:500]
            log.response_payload = {}
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
