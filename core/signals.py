"""
Iraniu — Django signals.

- post_save on AdminProfile: when created or is_notified set to True (first time), send welcome Telegram message.
- post_save on AdRequest: when a new request is created, notify all admins with Persian message and panel link.
- pre_save / post_save on AdRequest: detect status change to APPROVED and trigger distribution
  as a safety net (normally approve_one_ad handles this, but this covers Django Admin manual saves).
"""

import logging
import threading
from django.db.models.signals import post_save, pre_save, post_delete
from django.dispatch import receiver

from core.models import AdRequest, AdminProfile, SiteConfiguration, SystemLog, TelegramBot, TelegramChannel
from core.services.admin_notifications import send_admin_notification
from core.bot_handler import send_message_to_chat
from core.services.activity_log import log_activity
from core.services.log_service import log_event

logger = logging.getLogger(__name__)

# Store previous is_notified per AdminProfile pk for post_save (updates only).
_admin_old_is_notified: dict[int, bool] = {}

# Store previous ad status per AdRequest pk to detect transitions to APPROVED.
_ad_old_status: dict[int, str] = {}
# Track ads currently being approved via approve_one_ad (to avoid double-distribution).
_ads_approving_via_action: set[int] = set()

WELCOME_MESSAGE = (
    "🎉 خوش آمدید! شما اکنون به عنوان مدیر سیستم انتخاب شدید. "
    "از این پس اعلان‌های درخواست‌های جدید را اینجا دریافت خواهید کرد."
)


@receiver(pre_save, sender=AdminProfile)
def _admin_profile_pre_save(sender, instance, **kwargs):
    """Remember is_notified before save so we can detect first-time True in post_save."""
    if instance.pk:
        try:
            old = AdminProfile.objects.get(pk=instance.pk)
            _admin_old_is_notified[instance.pk] = old.is_notified
        except AdminProfile.DoesNotExist:
            pass


@receiver(post_save, sender=AdminProfile)
def on_admin_profile_save(sender, instance, created, **kwargs):
    """When an admin is created or is_notified is set to True for the first time, send welcome message."""
    tid = (instance.telegram_id or "").strip()
    if not tid or not instance.is_notified:
        if instance.pk in _admin_old_is_notified:
            _admin_old_is_notified.pop(instance.pk, None)
        return
    should_send = False
    if created:
        should_send = True
    else:
        old = _admin_old_is_notified.pop(instance.pk, None)
        if old is False:
            should_send = True
    if not should_send:
        return
    try:
        success, err = send_message_to_chat(tid, WELCOME_MESSAGE)
        if not success:
            logger.warning(
                "on_admin_profile_save: failed to send welcome to admin pk=%s telegram_id=%s: %s",
                instance.pk, tid, err,
            )
    except Exception as e:
        logger.exception("on_admin_profile_save: unexpected error: %s", e)


@receiver(pre_save, sender=AdRequest)
def _ad_request_pre_save(sender, instance, **kwargs):
    """Remember old status so post_save can detect transitions to APPROVED."""
    if instance.pk:
        try:
            old = AdRequest.objects.only('status').get(pk=instance.pk)
            _ad_old_status[instance.pk] = old.status
        except AdRequest.DoesNotExist:
            pass


@receiver(post_save, sender=AdRequest)
def on_ad_status_changed_to_approved(sender, instance, created, **kwargs):
    """
    Safety-net: if an ad transitions to APPROVED outside of approve_one_ad
    (e.g., Django Admin manual save), trigger background distribution.
    approve_one_ad marks the ad pk in _ads_approving_via_action to avoid duplicates.
    """
    if created:
        # New ad — handled separately by on_ad_request_created below
        _ad_old_status.pop(instance.pk, None)
        return

    old_status = _ad_old_status.pop(instance.pk, None)
    if old_status == instance.status:
        return  # no status change

    # Log every Ad status change to SystemLog (INFO)
    log_event(
        SystemLog.Level.INFO,
        SystemLog.Category.SYSTEM_CORE,
        f"Ad status changed: {old_status or 'new'} → {instance.status}",
        ad_request=instance,
        metadata={'old_status': old_status, 'new_status': instance.status},
    )

    if instance.status != AdRequest.Status.APPROVED:
        return  # not approving

    # If approve_one_ad already triggered distribution, skip
    if instance.pk in _ads_approving_via_action:
        _ads_approving_via_action.discard(instance.pk)
        return

    logger.info(
        "Signal: ad %s changed to APPROVED (from %s) outside approve_one_ad — triggering distribution",
        instance.uuid, old_status,
    )
    from core.services.ad_actions import _run_full_delivery
    thread = threading.Thread(
        target=_run_full_delivery,
        args=(instance.pk,),
        name=f"signal-distribute-{instance.uuid}",
        daemon=True,
    )
    thread.start()


@receiver(post_save, sender=AdRequest)
def on_ad_request_created(sender, instance, created, **kwargs):
    """When a new AdRequest is created, notify all admins with Persian message and panel link."""
    if not created:
        return
    try:
        user_phone = "—"
        if instance.contact_snapshot and isinstance(instance.contact_snapshot, dict):
            user_phone = (instance.contact_snapshot.get("phone") or "").strip() or user_phone
        if user_phone == "—" and instance.user_id and instance.user:
            user_phone = (getattr(instance.user, "phone_number", None) or "").strip() or user_phone
        if user_phone == "—" and (instance.telegram_username or "").strip():
            user_phone = (instance.telegram_username or "").strip()
        if user_phone == "—":
            user_phone = (
                getattr(instance.user, "username", None)
                and f"@{instance.user.username}"
            ) or (getattr(instance.user, "first_name", None) or "") or "نامشخص"

        config = SiteConfiguration.get_config()
        base = (config.production_base_url or "").strip().rstrip("/")
        panel_url = f"{base}/admin-management/" if base else ""

        request_details = {
            "title": (instance.content or "").strip()[:80],
            "content_preview": (instance.content or "").strip()[:80],
            "user_phone": user_phone,
            "user_display": user_phone,
            "created_at": instance.created_at,
            "panel_url": panel_url,
            "request_id": instance.pk,
            "uuid": str(instance.uuid)[:8],
        }
        send_admin_notification(request_details)
    except Exception as e:
        logger.exception("on_ad_request_created: failed to send admin notification: %s", e)


@receiver(post_save, sender=SiteConfiguration)
def on_site_config_saved(sender, instance, created, **kwargs):
    """Audit SiteConfiguration changes."""
    log_activity(
        action="Created site configuration" if created else "Updated site configuration",
        object_type="SiteConfiguration",
        object_repr=f"pk={instance.pk}",
    )


@receiver(post_save, sender=TelegramBot)
def on_bot_saved(sender, instance, created, **kwargs):
    """Audit Telegram bot changes."""
    log_activity(
        action="Created Telegram bot" if created else "Updated Telegram bot",
        object_type="TelegramBot",
        object_repr=f"{instance.name} (@{instance.username or '?'})",
    )


@receiver(post_save, sender=TelegramChannel)
def on_channel_saved(sender, instance, created, **kwargs):
    """Audit Telegram channel changes."""
    log_activity(
        action="Created Telegram channel" if created else "Updated Telegram channel",
        object_type="TelegramChannel",
        object_repr=f"{instance.title} ({instance.channel_id})",
    )


@receiver(post_delete, sender=TelegramBot)
def on_bot_deleted(sender, instance, **kwargs):
    log_activity(
        action="Deleted Telegram bot",
        object_type="TelegramBot",
        object_repr=f"{instance.name} (@{instance.username or '?'})",
    )


@receiver(post_delete, sender=TelegramChannel)
def on_channel_deleted(sender, instance, **kwargs):
    log_activity(
        action="Deleted Telegram channel",
        object_type="TelegramChannel",
        object_repr=f"{instance.title} ({instance.channel_id})",
    )
