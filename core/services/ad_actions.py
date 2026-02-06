"""
Iraniu — Approve/reject ad actions. Single place for status update + delivery.
Approval delivery (Telegram, Instagram, API) is delegated to DeliveryService.
"""

import logging
from django.utils import timezone

from core.models import AdRequest, SiteConfiguration
from core.services import (
    clean_ad_text,
    send_telegram_message,
    send_telegram_message_via_bot,
    send_telegram_rejection_with_button,
    send_telegram_rejection_with_button_via_bot,
)
from core.services.delivery import DeliveryService

logger = logging.getLogger(__name__)


def approve_one_ad(ad: AdRequest, edited_content: str | None = None, approved_by=None) -> None:
    """
    Set ad to approved, optionally update content, then send to all delivery channels.
    Caller must have already validated ad is in PENDING_AI or PENDING_MANUAL.
    Delivery (Telegram, Instagram, API) and DeliveryLog are handled by DeliveryService.
    approved_by: optional User instance for audit logging (who approved and when).
    """
    config = SiteConfiguration.get_config()
    if edited_content is not None:
        ad.content = clean_ad_text(edited_content)
    ad.status = AdRequest.Status.APPROVED
    ad.approved_at = timezone.now()
    ad.rejection_reason = ""
    ad.save()

    if approved_by is not None:
        logger.info(
            "Ad approved: uuid=%s by=%s at=%s",
            ad.uuid,
            getattr(approved_by, 'username', None) or getattr(approved_by, 'id', None),
            ad.approved_at,
        )

    for channel in DeliveryService.SUPPORTED_CHANNELS:
        try:
            DeliveryService.send(ad, channel)
        except Exception as e:
            logger.exception("approve_one_ad delivery channel=%s ad=%s: %s", channel, ad.uuid, e)


def reject_one_ad(ad: AdRequest, reason: str, rejected_by=None) -> None:
    """
    Set ad to rejected, store reason, send rejection notification with Edit & Resubmit.
    Caller must have already validated ad is in PENDING_AI or PENDING_MANUAL.
    rejected_by: optional User instance for audit logging (who rejected and when).
    """
    config = SiteConfiguration.get_config()
    ad.status = AdRequest.Status.REJECTED
    ad.rejection_reason = reason[:1000]
    ad.save()

    if rejected_by is not None:
        logger.info(
            "Ad rejected: uuid=%s reason=%s by=%s at=%s",
            ad.uuid,
            reason[:100],
            getattr(rejected_by, 'username', None) or getattr(rejected_by, 'id', None),
            timezone.now(),
        )

    msg = (
        config.rejection_message_template
        or "Your ad was not approved. Reason: {reason}. Ad ID: {ad_id}."
    ).format(reason=reason, ad_id=str(ad.uuid))
    if ad.telegram_user_id:
        if ad.bot and ad.bot.is_active:
            send_telegram_rejection_with_button_via_bot(
                ad.telegram_user_id, msg, str(ad.uuid), ad.bot
            )
        else:
            send_telegram_rejection_with_button(
                ad.telegram_user_id, msg, str(ad.uuid), config
            )
