"""
Iraniu — Process one Telegram update (shared by webhook and polling worker).

This module delegates to the unified dispatcher. Both the Polling worker and the Webhook view
must use the same entry point: process_update_payload in core.services.telegram_dispatcher.

Backward compatibility: process_update(bot, update) and lock/dedup helpers re-export from dispatcher.
"""

from core.models import TelegramBot
from core.services.telegram_dispatcher import (
    LAST_PROCESSED_UPDATE_ID_KEY,
    acquire_processing_lock,
    process_update_payload,
    should_skip_duplicate_update,
)

__all__ = [
    "LAST_PROCESSED_UPDATE_ID_KEY",
    "acquire_processing_lock",
    "should_skip_duplicate_update",
    "process_update",
]


def process_update(bot: TelegramBot, update: dict) -> None:
    """
    Process a single Telegram update. Thin wrapper around the unified dispatcher.
    Used by the Polling worker (bot_worker). Webhook view calls process_update_payload directly.
    """
    process_update_payload(bot, update)
