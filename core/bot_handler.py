"""
Iraniu — Centralized Telegram Bot initialization and lifecycle.

- Validates token via getMe; handles 401 Unauthorized without crashing.
- clear_webhook(): delete webhook with drop_pending_updates before polling.
- Multi-OS safe: no hardcoded path separators; use pathlib/os where needed.
"""

import logging
from typing import Optional, Tuple

from core.services.telegram_client import get_me, delete_webhook as _delete_webhook_api

logger = logging.getLogger(__name__)


class BotHandlerError(Exception):
    """Raised when bot operation fails (e.g. invalid token)."""
    pass


def validate_token(token: str) -> Tuple[bool, Optional[str], Optional[dict]]:
    """
    Validate bot token with getMe. Handles 401 Unauthorized gracefully.

    Returns:
        (success, error_message, bot_info)
        - success: True if getMe returned ok and bot_info is present.
        - error_message: None on success; short message on failure (e.g. "Unauthorized").
        - bot_info: getMe result dict (username, id, etc.) or None.
    """
    if not (token or "").strip():
        return False, "No token provided", None
    try:
        success, result, error = get_me(token.strip())
        if success and result:
            return True, None, result
        # 401 / Unauthorized: do not raise, do not crash — return clean error
        err = (error or "Unknown error").strip()
        if "401" in err or "unauthorized" in err.lower() or "invalid" in err.lower():
            logger.debug("Bot token validation failed (Unauthorized): %s", err[:100])
            return False, "Invalid or expired token (401 Unauthorized)", None
        return False, err[:500], None
    except Exception as e:
        logger.exception("validate_token unexpected error")
        return False, str(e)[:500], None


def clear_webhook(token: str, drop_pending_updates: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Remove webhook for the bot. Call before starting polling.

    Args:
        token: Bot token.
        drop_pending_updates: If True, Telegram drops pending updates (recommended when switching to polling).

    Returns:
        (success, error_message)
    """
    if not (token or "").strip():
        return False, "No token provided"
    try:
        ok, err = _delete_webhook_api(token.strip(), drop_pending_updates=drop_pending_updates)
        if not ok:
            logger.warning("clear_webhook failed: %s", err)
        return ok, err
    except Exception as e:
        logger.exception("clear_webhook unexpected error")
        return False, str(e)[:500]


def initialize_for_polling(token: str) -> Tuple[bool, Optional[str], Optional[str]]:
    """
    Validate token and clear webhook. Safe to call at startup; 401 does not raise.

    Returns:
        (success, error_message, username)
        username is without @ (e.g. "MyBot"); None if validation failed.
    """
    ok, err, bot_info = validate_token(token)
    if not ok:
        return False, err, None
    username = (bot_info or {}).get("username") if bot_info else None
    ok_del, err_del = clear_webhook(token, drop_pending_updates=True)
    if not ok_del:
        logger.warning("initialize_for_polling: webhook clear failed: %s", err_del)
        return False, "Cannot start polling: Webhook is still active on Telegram servers.", None
    return True, None, username


def _normalize_telegram_id(telegram_id: str) -> str:
    """Strip whitespace and keep only digits (reject @username / non-numeric)."""
    raw = (telegram_id or "").strip()
    digits = "".join(c for c in raw if c.isdigit())
    return digits


def _looks_like_telegram_bot_token(token: str) -> bool:
    """
    Sanity check: Telegram bot tokens are digits:alphanumeric (e.g. 123456789:AAH...).
    Used to avoid sending an encrypted blob or garbage to the API (decryption mismatch).
    """
    if not token or len(token) < 30:
        return False
    parts = token.strip().split(":", 1)
    if len(parts) != 2:
        return False
    left, right = parts[0], parts[1]
    if not left.isdigit() or not right:
        return False
    # Right part is base64-like (letters, digits, -, _)
    return all(c.isalnum() or c in "-_" for c in right)


def send_message_to_chat(telegram_id: str, text: str) -> Tuple[bool, Optional[str]]:
    """
    Send a text message to a Telegram chat using the default active bot.
    Used for admin notifications (welcome, new request, test ping).
    Catches invalid telegram_id, blocked bot, and network errors; logs exact API error and never raises.

    Returns:
        (success, error_message). error_message is None on success; on failure contains
        the Telegram API description when available (e.g. "Forbidden: bot was blocked by the user")
        so the UI can display it.
    """
    from django.conf import settings
    from core.models import TelegramBot
    from core.services.telegram_client import send_message

    tid = _normalize_telegram_id(telegram_id)
    if not tid:
        return False, "Empty or invalid telegram_id (use numeric ID only, not @username)"

    try:
        chat_id = int(tid)
    except (ValueError, TypeError):
        logger.warning("send_message_to_chat: invalid telegram_id after normalize %r", telegram_id)
        return False, "Invalid telegram_id (must be numeric)"

    env = getattr(settings, "ENVIRONMENT", "PROD")
    default_bot = (
        TelegramBot.objects.filter(environment=env, is_active=True)
        .order_by("-is_default")
        .first()
    )
    if not default_bot:
        logger.critical(
            "No active bot found for environment [%s]. Add a TelegramBot with environment=%s and is_active=True.",
            env, env,
        )
        return False, f"No active bot found for environment [{env}]."

    try:
        token = default_bot.get_decrypted_token()
    except Exception as e:
        logger.warning("send_message_to_chat: could not get token: %s", e)
        return False, "Token decryption failed. Re-save the bot token in the Bots page."
    if not token:
        logger.warning("send_message_to_chat: default bot %s has empty token (decryption failed or never set)", default_bot.pk)
        return False, "Bot token missing or decryption failed. Re-save the token in the Bots page."
    if not _looks_like_telegram_bot_token(token):
        logger.warning(
            "send_message_to_chat: default bot %s token does not look like a Telegram token (encrypted blob or wrong format). Re-save token.",
            default_bot.pk,
        )
        return False, "Token invalid or decryption mismatch. Re-save the bot token in the Bots page (same SECRET_KEY as when it was saved)."

    success, _, api_error = send_message(token, chat_id, text)
    if success:
        return True, None

    # Log the EXACT Telegram API response
    exact_error = (api_error or "Unknown error").strip()
    logger.warning(
        "send_message_to_chat: Telegram API error for chat_id=%s: %s",
        chat_id,
        exact_error,
    )
    if "forbidden" in exact_error.lower() and "blocked" in exact_error.lower():
        logger.warning("send_message_to_chat: bot was blocked by the user (chat_id=%s)", chat_id)
    if "chat not found" in exact_error.lower() or "bad request" in exact_error.lower():
        logger.warning(
            "send_message_to_chat: chat not found or bad request (chat_id=%s). User may not have started the bot.",
            chat_id,
        )

    # Return the exact API description so the test view can show it in the alert
    return False, exact_error or "Send failed"
