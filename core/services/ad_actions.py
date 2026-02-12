"""
Iraniu — Approve/reject ad actions. Single place for status update + delivery.
Approval delivery (Telegram DM, Channel post, Instagram, Story, API) is delegated to DeliveryService.
Distribution to external platforms runs in a background thread so the admin UI stays responsive.
"""

import logging
import threading
from django.utils import timezone

from core.models import AdRequest, SiteConfiguration, TelegramSession
from core.i18n import get_message, get_category_display_name
from core.services import (
    clean_ad_text,
    send_telegram_message,
    send_telegram_message_via_bot,
    send_telegram_rejection_with_button,
    send_telegram_rejection_with_button_via_bot,
)
from core.services.delivery import DeliveryService

logger = logging.getLogger(__name__)


def _run_full_delivery(ad_pk: int) -> None:
    """
    Background worker: deliver the approved ad to every channel.
    Runs in a separate thread so the admin save/approve action doesn't block
    waiting for Telegram/Instagram API round-trips.

    Each channel is independent — a failure in one does NOT stop the others.
    After all channels complete, a summary notification is fired.
    """
    import django
    django.setup()  # ensure Django is initialized in the thread

    from core.models import AdRequest as AR, DeliveryLog
    from core.notifications import send_notification

    try:
        ad = AR.objects.select_related('category', 'user', 'bot').get(pk=ad_pk)
    except AR.DoesNotExist:
        logger.error("_run_full_delivery: ad pk=%s not found", ad_pk)
        return

    # Max retries for slow/external channels (Telegram Channel, Instagram, Story)
    MAX_CHANNEL_RETRIES = 3
    RETRY_DELAY_SECONDS = 5

    results = {}
    for channel in DeliveryService.SUPPORTED_CHANNELS:
        retries = MAX_CHANNEL_RETRIES if channel in DeliveryService.SLOW_CHANNELS else 0
        ok = False
        for attempt in range(retries + 1):
            try:
                ok = DeliveryService.send(ad, channel)
                if ok:
                    break
            except Exception as e:
                logger.exception(
                    "_run_full_delivery channel=%s ad=%s attempt=%s/%s: %s",
                    channel, ad.uuid, attempt + 1, retries + 1, e,
                )
            if attempt < retries:
                import time
                logger.info(
                    "_run_full_delivery: retrying channel=%s ad=%s in %ss (attempt %s/%s)",
                    channel, ad.uuid, RETRY_DELAY_SECONDS, attempt + 2, retries + 1,
                )
                time.sleep(RETRY_DELAY_SECONDS)
        results[channel] = ok

    # Build summary
    succeeded = [ch for ch, ok in results.items() if ok]
    failed = [ch for ch, ok in results.items() if not ok]

    # Map channel codes to display names
    display_names = {
        'telegram': 'Telegram DM',
        'telegram_channel': 'Telegram Channel',
        'instagram': 'Instagram Post',
        'instagram_story': 'Instagram Story',
        'api': 'API',
    }

    if failed:
        failed_names = ", ".join(display_names.get(ch, ch) for ch in failed)
        succeeded_names = ", ".join(display_names.get(ch, ch) for ch in succeeded) if succeeded else "—"
        try:
            send_notification(
                level='warning',
                message=(
                    f"آگهی {str(ad.uuid)[:8]} — توزیع ناقص.\n"
                    f"✅ موفق: {succeeded_names}\n"
                    f"❌ ناموفق: {failed_names}"
                ),
                link=f"/ad/{ad.uuid}/",
            )
        except Exception:
            logger.exception("_run_full_delivery: notification error")
    else:
        try:
            send_notification(
                level='success',
                message=f"آگهی {str(ad.uuid)[:8]} با موفقیت در تمامی پلتفرم‌ها منتشر شد.",
                link=f"/ad/{ad.uuid}/",
            )
        except Exception:
            logger.exception("_run_full_delivery: notification error")

    logger.info(
        "Distribution complete ad=%s succeeded=%s failed=%s",
        ad.uuid,
        succeeded,
        failed,
    )


def approve_one_ad(ad: AdRequest, edited_content: str | None = None, approved_by=None) -> None:
    """
    Set ad to approved, optionally update content, then kick off distribution
    to ALL delivery channels in a background thread.

    Caller must have already validated ad is in PENDING_AI or PENDING_MANUAL.
    Delivery (Telegram DM + Channel, Instagram Feed + Story, API) and DeliveryLog
    are handled by DeliveryService inside the background thread.
    approved_by: optional User instance for audit logging (who approved and when).
    """
    if edited_content is not None:
        ad.content = clean_ad_text(edited_content)
    ad.status = AdRequest.Status.APPROVED
    ad.approved_at = timezone.now()
    ad.rejection_reason = ""

    # Mark ad so the post_save signal in signals.py skips duplicate distribution
    from core.signals import _ads_approving_via_action
    _ads_approving_via_action.add(ad.pk)
    ad.save()

    if approved_by is not None:
        logger.info(
            "Ad approved: uuid=%s by=%s at=%s",
            ad.uuid,
            getattr(approved_by, 'username', None) or getattr(approved_by, 'id', None),
            ad.approved_at,
        )

    # Launch distribution in background thread so admin UI is not blocked
    thread = threading.Thread(
        target=_run_full_delivery,
        args=(ad.pk,),
        name=f"distribute-{ad.uuid}",
        daemon=True,
    )
    thread.start()
    logger.info("Distribution thread started for ad=%s", ad.uuid)


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

    lang = "en"
    if ad.telegram_user_id and ad.bot_id:
        session = TelegramSession.objects.filter(
            telegram_user_id=ad.telegram_user_id, bot_id=ad.bot_id
        ).first()
        if session and session.language:
            lang = session.language
    category_name = (ad.category.name if ad.category else get_category_display_name("other", lang))
    msg = get_message("notification_rejected", lang).format(
        category=category_name, reason=reason
    )
    if ad.telegram_user_id:
        if ad.bot and ad.bot.is_active:
            send_telegram_rejection_with_button_via_bot(
                ad.telegram_user_id, msg, str(ad.uuid), ad.bot
            )
        else:
            send_telegram_rejection_with_button(
                ad.telegram_user_id, msg, str(ad.uuid), config
            )


def request_revision_one_ad(ad: AdRequest, requested_by=None) -> None:
    """
    Set ad to NEEDS_REVISION and send Telegram notification with Edit & Resubmit button.
    Caller must have already validated ad is in PENDING_AI or PENDING_MANUAL.
    """
    ad.status = AdRequest.Status.NEEDS_REVISION
    ad.save(update_fields=["status"])

    if requested_by is not None:
        logger.info(
            "Ad needs revision: uuid=%s by=%s at=%s",
            ad.uuid,
            getattr(requested_by, "username", None) or getattr(requested_by, "id", None),
            timezone.now(),
        )

    if not ad.telegram_user_id:
        return
    lang = "en"
    if ad.bot_id:
        session = TelegramSession.objects.filter(
            telegram_user_id=ad.telegram_user_id, bot_id=ad.bot_id
        ).first()
        if session and session.language:
            lang = session.language
    category_name = (ad.category.name if ad.category else get_category_display_name("other", lang))
    msg = get_message("notification_needs_revision", lang).format(category=category_name)
    if ad.bot and ad.bot.is_active:
        send_telegram_rejection_with_button_via_bot(
            ad.telegram_user_id, msg, str(ad.uuid), ad.bot
        )
    else:
        config = SiteConfiguration.get_config()
        send_telegram_rejection_with_button(
            ad.telegram_user_id, msg, str(ad.uuid), config
        )
