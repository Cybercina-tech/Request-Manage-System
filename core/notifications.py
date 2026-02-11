"""
Iraniu — Internal notification system. Use send_notification() from anywhere to create
persistent system-wide alerts (Success, Info, Warning, Error) with optional link.
"""

import logging
from typing import Optional

from django.utils import timezone

from core.models import Notification, SystemStatus

logger = logging.getLogger(__name__)


def send_notification(
    level: str,
    message: str,
    link: Optional[str] = None,
    *,
    add_to_active_errors: bool = False,
) -> Notification:
    """
    Create a persistent notification. Use from views, tasks, or services.

    :param level: One of 'success', 'info', 'warning', 'error'.
    :param message: Notification text (supports Persian/RTL).
    :param link: Optional URL (e.g. to fix the issue).
    :param add_to_active_errors: If True and level is 'error', append message to SystemStatus.active_errors.
    :return: Created Notification instance.
    """
    level = (level or 'info').lower().strip()
    if level not in dict(Notification.Level.choices):
        level = 'info'
    msg = (message or '').strip()
    if not msg:
        msg = 'Notification'
    link = (link or '').strip() or ''
    try:
        notification = Notification.objects.create(
            level=level,
            message=msg[:4096],
            link=link[:512] if link else '',
        )
        try:
            from django.core.cache import cache
            cache.delete('navbar_notifications')
        except Exception:
            pass
        if add_to_active_errors and level == Notification.Level.ERROR:
            try:
                status = SystemStatus.get_status()
                status.add_active_error(msg)
            except Exception as e:
                logger.warning("add_active_error failed: %s", e)
        return notification
    except Exception as e:
        logger.exception("send_notification failed: %s", e)
        raise
