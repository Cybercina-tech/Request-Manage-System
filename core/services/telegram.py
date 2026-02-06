"""
Iraniu — Telegram Bot API. Send messages, webhooks, resubmit links.
Thin wrapper around telegram_client for backward compatibility.
"""

import logging

from .telegram_client import (
    send_message as _send_message,
    edit_message_text as _edit_message_text,
    answer_callback_query as _answer_callback_query,
    get_me as _get_me,
    get_webhook_info as _get_webhook_info,
    set_webhook as _set_webhook,
    delete_webhook as _delete_webhook,
)

logger = logging.getLogger(__name__)


def send_telegram_message(chat_id, text, config, reply_markup=None):
    """Send a message via Telegram Bot API (config token). reply_markup: dict e.g. { inline_keyboard: [...] }. Returns success bool."""
    token = (config.telegram_bot_token or '').strip()
    if not token or not chat_id:
        return False
    ok, _ = _send_message(token, chat_id, text, reply_markup=reply_markup)
    return ok


def send_telegram_message_via_bot(chat_id, text, bot, reply_markup=None):
    """
    Send a message using a TelegramBot instance. Retries on failure.
    Returns message_id (int) on success so caller can store in session.context['last_bot_message_id'];
    returns None on failure.
    """
    if not bot or not bot.is_active:
        return None
    token = bot.get_decrypted_token()
    if not token or not chat_id:
        return None
    ok, message_id = _send_message(token, chat_id, text, reply_markup=reply_markup)
    return message_id if ok else None


def edit_message_text_via_bot(chat_id, message_id, text, bot, reply_markup=None):
    """
    Edit a message using a TelegramBot instance (only bot messages can be edited).
    Use for inline button callbacks. Returns True if edited, False if edit failed
    (e.g. message deleted, too old, or not from bot). Caller should fallback to
    send_telegram_message_via_bot with the same text/reply_markup when this returns False.
    """
    if not bot or not bot.is_active:
        return False
    token = bot.get_decrypted_token()
    if not token or not chat_id or not message_id:
        return False
    try:
        return _edit_message_text(token, chat_id, message_id, text, reply_markup=reply_markup)
    except Exception as e:
        logger.warning("edit_message_text_via_bot exception: %s", e)
        return False


def answer_callback_query_via_bot(callback_query_id, bot, text=None, show_alert=False):
    """Answer callback query to remove loading state. Returns success bool."""
    if not bot or not bot.is_active or not callback_query_id:
        return False
    token = bot.get_decrypted_token()
    if not token:
        return False
    return _answer_callback_query(token, callback_query_id, text=text, show_alert=show_alert)


def send_telegram_rejection_with_button(chat_id, text, ad_uuid, config):
    """
    Send rejection message with inline "Edit & Resubmit" button.
    Uses config.telegram_bot_username to build https://t.me/BotName?start=resubmit_{uuid}.
    """
    username = (config.telegram_bot_username or '').strip().lstrip('@')
    if username:
        url = f'https://t.me/{username}?start=resubmit_{ad_uuid}'
        reply_markup = {'inline_keyboard': [[{'text': 'Edit & Resubmit', 'url': url}]]}
    else:
        reply_markup = {'inline_keyboard': [[{'text': 'Edit & Resubmit', 'callback_data': f'resubmit_{ad_uuid}'}]]}
    return send_telegram_message(chat_id, text, config, reply_markup=reply_markup)


def send_telegram_rejection_with_button_via_bot(chat_id, text, ad_uuid, bot):
    """Send rejection with Edit & Resubmit button using TelegramBot."""
    username = (bot.username or '').strip().lstrip('@')
    if username:
        url = f'https://t.me/{username}?start=resubmit_{ad_uuid}'
        reply_markup = {'inline_keyboard': [[{'text': 'Edit & Resubmit', 'url': url}]]}
    else:
        reply_markup = {'inline_keyboard': [[{'text': 'Edit & Resubmit', 'callback_data': f'resubmit_{ad_uuid}'}]]}
    return send_telegram_message_via_bot(chat_id, text, bot, reply_markup=reply_markup)


def test_telegram_connection(token):
    """
    Test Telegram Bot API connectivity. Returns (success, message).
    Backward-compatible wrapper around telegram_client.get_me.
    """
    if not (token or '').strip():
        return False, 'No token provided'
    
    success, bot_info, error = _get_me(token.strip())
    if success and bot_info:
        username = bot_info.get('username', '?')
        return True, f"Connected as @{username}"
    
    return False, error or 'Unknown error'


def get_webhook_info(token):
    """
    Get current webhook info. Returns (ok, result_dict or error_message).
    Backward-compatible wrapper around telegram_client.get_webhook_info.
    """
    if not (token or '').strip():
        return False, 'No token'
    
    success, result, error = _get_webhook_info(token.strip())
    if success:
        return True, result or {}
    
    return False, error or 'Unknown error'


def set_webhook(token, url, secret_token=None):
    """
    Set webhook URL. Returns (success, message).
    Backward-compatible wrapper around telegram_client.set_webhook.
    """
    if not (token or '').strip():
        return False, 'No token'
    
    success, message = _set_webhook(token.strip(), url, secret_token=secret_token)
    return success, message or 'Unknown error'


def delete_webhook(token):
    """
    Remove webhook. Returns (success, message).
    Backward-compatible wrapper around telegram_client.delete_webhook.
    """
    if not (token or '').strip():
        return False, 'No token'
    
    success, message = _delete_webhook(token.strip())
    return success, message or 'Unknown error'
