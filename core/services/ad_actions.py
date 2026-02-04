"""
Iranio — Approve/reject ad actions. Single place for status update + Telegram notify.
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

logger = logging.getLogger(__name__)


def approve_one_ad(ad: AdRequest, edited_content: str | None = None) -> None:
    """
    Set ad to approved, optionally update content, send approval notification.
    Caller must have already validated ad is in PENDING_AI or PENDING_MANUAL.
    """
    config = SiteConfiguration.get_config()
    if edited_content is not None:
        ad.content = clean_ad_text(edited_content)
    ad.status = AdRequest.Status.APPROVED
    ad.approved_at = timezone.now()
    ad.rejection_reason = ""
    ad.save()

    msg = (config.approval_message_template or "Your ad has been approved. Ad ID: {ad_id}.").format(
        ad_id=str(ad.uuid)
    )
    if ad.telegram_user_id:
        if ad.bot and ad.bot.is_active:
            send_telegram_message_via_bot(ad.telegram_user_id, msg, ad.bot)
        else:
            send_telegram_message(ad.telegram_user_id, msg, config)


def reject_one_ad(ad: AdRequest, reason: str) -> None:
    """
    Set ad to rejected, store reason, send rejection notification with Edit & Resubmit.
    Caller must have already validated ad is in PENDING_AI or PENDING_MANUAL.
    """
    config = SiteConfiguration.get_config()
    ad.status = AdRequest.Status.REJECTED
    ad.rejection_reason = reason[:1000]
    ad.save()

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
