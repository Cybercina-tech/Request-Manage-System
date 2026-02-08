"""
Iraniu — Unified delivery layer. Routes approved ads to Telegram, Instagram, API.
Centralized error handling and DeliveryLog. Business logic only; no request objects.
"""

import logging
from django.utils import timezone

from core.models import (
    AdRequest,
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
    SUPPORTED_CHANNELS = ('telegram', 'instagram', 'api')

    @staticmethod
    def send(ad: AdRequest, channel: str) -> bool:
        """
        Deliver ad to the given channel. Returns True if delivery succeeded.
        Validates ad is approved; creates DeliveryLog (pending → success/failed).
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
            elif channel == 'instagram':
                ok = DeliveryService._send_instagram(ad, log)
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

    @staticmethod
    def _send_telegram(ad: AdRequest) -> bool:
        """Send approval notification via Telegram (localized, category, no Ad ID)."""
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

    @staticmethod
    def _send_instagram(ad: AdRequest, log: DeliveryLog | None = None) -> bool:
        """Post ad to Instagram via InstagramService. Populates log.error_message on failure."""
        from core.services.instagram import InstagramService

        result = InstagramService.post_ad(ad)
        if isinstance(result, dict) and result.get('success'):
            return True
        if log is not None and isinstance(result, dict) and result.get('message'):
            log.error_message = result.get('message', '')[:500]
        return False

    @staticmethod
    def _send_api(ad: AdRequest) -> bool:
        """
        Record ad as delivered to API channel (partners can fetch via /api/v1/list/).
        No outbound HTTP; availability via API is the delivery.
        """
        # Ad is already in DB and approved; API clients see it via list/status. Success.
        return True
