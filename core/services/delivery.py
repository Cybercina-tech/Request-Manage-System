"""
Iranio — Unified delivery layer. Routes approved ads to Telegram, Instagram, API.
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
                ok = DeliveryService._send_instagram(ad)
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
        """Send approval notification via Telegram (legacy config or ad.bot)."""
        from core.services import send_telegram_message, send_telegram_message_via_bot

        config = SiteConfiguration.get_config()
        msg = (config.approval_message_template or "Your ad has been approved. Ad ID: {ad_id}.").format(
            ad_id=str(ad.uuid)
        )
        if not ad.telegram_user_id:
            logger.debug("DeliveryService._send_telegram: no telegram_user_id for ad %s", ad.uuid)
            return True  # nothing to send, consider success
        if ad.bot and ad.bot.is_active:
            return send_telegram_message_via_bot(ad.telegram_user_id, msg, ad.bot)
        return send_telegram_message(ad.telegram_user_id, msg, config)

    @staticmethod
    def _send_instagram(ad: AdRequest) -> bool:
        """Post ad to Instagram via InstagramService."""
        from core.services.instagram import InstagramService

        result = InstagramService.post_ad(ad)
        if isinstance(result, dict) and result.get('success'):
            return True
        return False

    @staticmethod
    def _send_api(ad: AdRequest) -> bool:
        """
        Record ad as delivered to API channel (partners can fetch via /api/v1/list/).
        No outbound HTTP; availability via API is the delivery.
        """
        # Ad is already in DB and approved; API clients see it via list/status. Success.
        return True
