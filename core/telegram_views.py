"""
Iranio — Telegram webhook endpoint. No business logic; delegates to conversation engine.
Validates secret token; rate limits per user.
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
    """
    bot = TelegramBot.objects.filter(pk=bot_id, is_active=True).first()
    if not bot:
        return HttpResponse(status=404)

    # Verify secret token if configured
    if bot.webhook_secret:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").strip()
        if secret != bot.webhook_secret:
            logger.warning("Webhook secret mismatch for bot_id=%s", bot_id)
            return HttpResponse(status=403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, TypeError):
        return HttpResponse(status=400)

    # Log raw update (optional)
    logger.debug("Telegram update bot_id=%s: %s", bot_id, json.dumps(body)[:500])

    user_id = _get_telegram_user_id(body)
    if not user_id:
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
        return HttpResponse(status=200)  # Not a message we handle; ack anyway

    # Create/update Telegram user before any conversation logic
    get_or_create_user_from_update(body)

    # Log inbound (optional)
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

    engine = ConversationEngine(bot)
    session = engine.get_or_create_session(user_id)
    response = engine.process_update(session, text=text, callback_data=callback_data)

    text_out = response.get("text")
    reply_markup = response.get("reply_markup")
    if text_out:
        send_telegram_message_via_bot(chat_id, text_out, bot, reply_markup=reply_markup)
        # Store message history (optional)
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

    return HttpResponse(status=200)


def _get_telegram_user_id(body: dict) -> int:
    """Extract telegram user id from update."""
    msg = body.get("message") or body.get("callback_query", {}).get("message") or {}
    from_user = (body.get("message") or {}).get("from") or (body.get("callback_query") or {}).get("from") or {}
    return int(from_user.get("id", 0))


def _parse_update(body: dict) -> tuple:
    """
    Parse update. Returns (chat_id, text, callback_data).
    chat_id None if no message/callback to process.
    """
    # Message
    message = body.get("message")
    if message:
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        return (chat_id, text or None, None)

    # Callback query
    callback = body.get("callback_query")
    if callback:
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        data = (callback.get("data") or "").strip()
        return (chat_id, None, data or None)

    return (None, None, None)
