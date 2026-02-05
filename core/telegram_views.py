"""
Iranio — Telegram webhook endpoint. No business logic; delegates to conversation engine.
Validates secret token; rate limits per user.

How updates work (webhook mode):
- Telegram sends POST to the URL you set via setWebhook (HTTPS required).
- This view receives the update, parses message/edited_message/callback_query,
  runs the conversation engine, and sends the reply via sendMessage.
- No polling: the Django server must be running and the webhook URL must be
  set in Telegram (Bots > Edit > Webhook URL, then save or Regenerate webhook).
- Run: start Django (e.g. gunicorn or runserver with HTTPS). Ensure the
  webhook URL is exactly: https://<your-domain>/telegram/webhook/<bot_id>/
"""

import json
import logging
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.cache import cache

from core.models import TelegramBot, TelegramMessageLog
from core.services.conversation import ConversationEngine
from core.services import send_telegram_message_via_bot
from core.services.users import get_or_create_user_from_update

logger = logging.getLogger(__name__)

# Rate limit: max requests per user per minute per bot
WEBHOOK_RATE_LIMIT = 30
WEBHOOK_RATE_WINDOW = 60  # seconds


@csrf_exempt
@require_http_methods(["POST"])
def telegram_webhook(request, bot_id: int):
    """
    Telegram webhook: verify secret, parse update, route to conversation engine, send reply.
    Always returns 200 on success or after handling errors so Telegram does not retry indefinitely.
    """
    bot = TelegramBot.objects.filter(pk=bot_id, is_active=True).first()
    if not bot:
        logger.warning("Webhook: bot_id=%s not found or inactive", bot_id)
        return HttpResponse(status=404)

    # Verify secret token if configured
    if bot.webhook_secret:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").strip()
        if secret != bot.webhook_secret:
            logger.warning("Webhook secret mismatch for bot_id=%s", bot_id)
            return HttpResponse(status=403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Webhook invalid JSON bot_id=%s: %s", bot_id, e)
        return HttpResponse(status=400)

    update_id = body.get("update_id")
    logger.info(
        "Telegram update received bot_id=%s update_id=%s",
        bot_id,
        update_id,
        extra={"update_keys": list(body.keys())},
    )

    user_id = _get_telegram_user_id(body)
    if not user_id:
        logger.debug("Webhook bot_id=%s update_id=%s: no user id (e.g. channel post), ack", bot_id, update_id)
        return HttpResponse(status=200)

    # Rate limit per user per bot
    cache_key = f"telegram_webhook_{bot_id}_{user_id}"
    count = cache.get(cache_key, 0) + 1
    cache.set(cache_key, count, timeout=WEBHOOK_RATE_WINDOW)
    if count > WEBHOOK_RATE_LIMIT:
        logger.warning("Rate limit exceeded bot_id=%s user_id=%s", bot_id, user_id)
        return HttpResponse(status=429)

    chat_id, text, callback_data = _parse_update(body)
    if chat_id is None:
        logger.debug("Webhook bot_id=%s update_id=%s: no message/callback to handle, ack", bot_id, update_id)
        return HttpResponse(status=200)

    logger.info(
        "Telegram message received bot_id=%s chat_id=%s user_id=%s text=%s callback=%s",
        bot_id,
        chat_id,
        user_id,
        (text or "")[:100] if text else None,
        callback_data,
    )

    # Create/update Telegram user before any conversation logic
    try:
        get_or_create_user_from_update(body)
    except Exception as e:
        logger.exception("Webhook get_or_create_user_from_update failed bot_id=%s: %s", bot_id, e)
        # Continue; we can still reply

    try:
        inbound_text = text or (f"[callback:{callback_data}]" if callback_data else "")
        if inbound_text:
            TelegramMessageLog.objects.create(
                bot=bot,
                telegram_user_id=user_id,
                direction="in",
                text=inbound_text[:4096],
                raw_payload=body,
            )
    except Exception as e:
        logger.debug("Message log create failed: %s", e)

    try:
        engine = ConversationEngine(bot)
        session = engine.get_or_create_session(user_id)
        response = engine.process_update(session, text=text, callback_data=callback_data)
    except Exception as e:
        logger.exception("Webhook conversation failed bot_id=%s user_id=%s: %s", bot_id, user_id, e)
        return HttpResponse(status=200)

    text_out = response.get("text")
    reply_markup = response.get("reply_markup")
    if text_out:
        try:
            sent = send_telegram_message_via_bot(chat_id, text_out, bot, reply_markup=reply_markup)
            logger.info(
                "Telegram reply sent bot_id=%s chat_id=%s sent=%s len=%s",
                bot_id,
                chat_id,
                sent,
                len(text_out),
            )
        except Exception as e:
            logger.exception("Webhook send_telegram_message_via_bot failed bot_id=%s chat_id=%s: %s", bot_id, chat_id, e)
        try:
            TelegramMessageLog.objects.create(
                bot=bot,
                telegram_user_id=user_id,
                direction="out",
                text=text_out[:4096],
                raw_payload={"reply_markup": reply_markup} if reply_markup else None,
            )
        except Exception as e:
            logger.debug("Message log create failed: %s", e)
    else:
        logger.debug("Webhook bot_id=%s: no text in response, nothing to send", bot_id)

    return HttpResponse(status=200)


def _get_telegram_user_id(body: dict) -> int:
    """Extract telegram user id from update (message, edited_message, or callback_query)."""
    from_user = (
        (body.get("message") or {}).get("from")
        or (body.get("edited_message") or {}).get("from")
        or (body.get("callback_query") or {}).get("from")
        or {}
    )
    try:
        return int(from_user.get("id", 0))
    except (TypeError, ValueError):
        return 0


def _parse_update(body: dict) -> tuple:
    """
    Parse update. Returns (chat_id, text, callback_data).
    chat_id None if no message/edited_message/callback to process.
    Supports: message, edited_message, callback_query.
    """
    # Message
    message = body.get("message")
    if message:
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        return (chat_id, text or None, None)

    # Edited message (treat like message)
    edited = body.get("edited_message")
    if edited:
        chat_id = edited.get("chat", {}).get("id")
        text = (edited.get("text") or "").strip()
        return (chat_id, text or None, None)

    # Callback query
    callback = body.get("callback_query")
    if callback:
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        data = (callback.get("data") or "").strip()
        return (chat_id, None, data or None)

    return (None, None, None)
