"""
Iraniu — Admin notification service.

Sends Telegram messages to staff admins (AdminProfile) when a new AdRequest is created.
Uses the default active bot via core.bot_handler.send_message_to_chat (robust, no crash).
"""

import logging
from typing import Any, Dict

from django.utils import timezone

from core.models import AdminProfile, SiteConfiguration
from core.bot_handler import send_message_to_chat

logger = logging.getLogger(__name__)


def _panel_url() -> str:
    """Base URL for admin panel (e.g. https://yourdomain.com/admin-management/)."""
    config = SiteConfiguration.get_config()
    base = (config.production_base_url or "").strip().rstrip("/")
    return f"{base}/admin-management/" if base else ""


def build_new_request_message(request_details: Dict[str, Any]) -> str:
    """
    Build Persian new-request notification text.
    request_details: title (content preview), user_phone, created_at, panel_url.
    """
    title = (request_details.get("title") or request_details.get("content_preview") or "—")[:80]
    user_phone = request_details.get("user_phone") or request_details.get("user_display") or "—"
    created_at = request_details.get("created_at")
    if created_at:
        try:
            if hasattr(created_at, "strftime"):
                created_str = created_at.strftime("%Y-%m-%d %H:%M")
            else:
                created_str = str(created_at)[:16]
        except Exception:
            created_str = str(created_at)
    else:
        created_str = "—"
    panel_url = request_details.get("panel_url") or _panel_url()
    link_line = f"\n\n[مشاهده در پنل]({panel_url})" if panel_url else ""
    return (
        "🔔 **درخواست جدید ثبت شد!**\n\n"
        f"📝 عنوان: {title}\n"
        f"📱 کاربر: {user_phone}\n"
        f"📅 زمان: {created_str}"
        f"{link_line}"
    )


def send_admin_notification(request_details: Dict[str, Any]) -> None:
    """
    Notify all admins with is_notified=True via Telegram using the default bot.
    Uses send_message_to_chat so invalid/blocked chats are logged and do not crash.

    request_details can contain:
        - title or content_preview: short title (e.g. first 80 chars of content)
        - user_phone or user_display: phone or username
        - created_at: datetime for display
        - panel_url: optional; if missing, uses SiteConfiguration.production_base_url
    """
    text = build_new_request_message(request_details)
    profiles = AdminProfile.objects.filter(is_notified=True).exclude(telegram_id="").select_related("user")
    for profile in profiles:
        tid = (profile.telegram_id or "").strip()
        if not tid:
            continue
        success, err = send_message_to_chat(tid, text)
        if not success:
            logger.warning(
                "send_admin_notification: failed to send to admin %s (telegram_id=%s): %s",
                profile.user.username,
                tid,
                err or "unknown",
            )
