"""
Iraniu — Telegram webhook endpoint. No business logic; delegates to conversation engine.
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
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.views import View
from django.utils.decorators import method_decorator
from django.core.cache import cache

from core.models import TelegramBot, TelegramMessageLog
from core.services.conversation import ConversationEngine
from core.services import (
    send_telegram_message_via_bot,
    edit_message_text_via_bot,
    answer_callback_query_via_bot,
)
from core.services.users import get_or_create_user_from_update
from core.services.telegram_update_handler import (
    acquire_processing_lock,
    should_skip_duplicate_update,
    LAST_PROCESSED_UPDATE_ID_KEY,
)

logger = logging.getLogger(__name__)

# Rate limit: max requests per user per minute per bot
WEBHOOK_RATE_LIMIT = 30
WEBHOOK_RATE_WINDOW = 60  # seconds


@method_decorator(csrf_exempt, name='dispatch')
class TelegramWebhookView(View):
    """
    Webhook gateway by secret UUID. Path: /telegram/webhook/<uuid:webhook_secret_token>/.
    Validates token, updates last_webhook_received, processes update. Always returns 200 so
    Telegram does not retry (processing errors are logged but not surfaced).
    """

    def post(self, request, webhook_secret_token):
        bot = TelegramBot.objects.filter(
            webhook_secret_token=webhook_secret_token, is_active=True
        ).first()
        if not bot:
            logger.warning("Webhook: token=%s not found or inactive", webhook_secret_token)
            return HttpResponse(status=404)
        # Optional: verify header matches (Telegram sends this when we set secret_token in setWebhook)
        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").strip()
        if secret_header and str(webhook_secret_token) != secret_header:
            logger.warning("Webhook secret header mismatch for bot_id=%s", bot.pk)
            return HttpResponse(status=403)
        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Webhook invalid JSON bot_id=%s: %s", bot.pk, e)
            return HttpResponse(status=400)
        # Update health as soon as we have valid JSON (so dashboard shows "active")
        TelegramBot.objects.filter(pk=bot.pk).update(last_webhook_received=timezone.now())
        update_id = body.get("update_id", "?")
        logger.info("Webhook ping received bot_id=%s update_id=%s", bot.pk, update_id)
        try:
            _process_webhook_payload(bot, body)
        except Exception as e:
            logger.warning("Webhook processing failed bot_id=%s update_id=%s: %s", bot.pk, update_id, e)
        return HttpResponse(status=200)


def _process_webhook_payload(bot, body):
    """Parse update, run conversation, send reply. Raises on error."""
    bot_id = bot.pk
    update_id = body.get("update_id")
    logger.info(
        "Telegram update received bot_id=%s update_id=%s",
        bot_id, update_id, extra={"update_keys": list(body.keys())},
    )
    user_id = _get_telegram_user_id(body)
    if not user_id:
        logger.debug("Webhook bot_id=%s update_id=%s: no user id", bot_id, update_id)
        return
    cache_key = f"telegram_webhook_{bot_id}_{user_id}"
    count = cache.get(cache_key, 0) + 1
    cache.set(cache_key, count, timeout=WEBHOOK_RATE_WINDOW)
    if count > WEBHOOK_RATE_LIMIT:
        logger.warning("Rate limit exceeded bot_id=%s user_id=%s", bot_id, user_id)
        return
    chat_id, text, callback_data, message_id, callback_query_id, contact_phone, contact_user_id = _parse_update(body)
    if chat_id is None:
        logger.debug("Webhook bot_id=%s update_id=%s: no message/callback", bot_id, update_id)
        return
    if not acquire_processing_lock(bot_id, update_id):
        logger.info("Webhook skip (lock held) update_id=%s bot_id=%s", update_id, bot_id)
        return
    engine = ConversationEngine(bot)
    session = engine.get_or_create_session(user_id)
    if should_skip_duplicate_update(session, update_id):
        logger.info("Webhook skip duplicate update_id=%s session_id=%s bot_id=%s", update_id, session.pk, bot_id)
        return
    logger.info(
        "Telegram message received bot_id=%s chat_id=%s user_id=%s update_id=%s text=%s callback=%s contact=%s",
        bot_id, chat_id, user_id, update_id, (text or "")[:100] if text else None, callback_data, bool(contact_phone),
    )
    try:
        get_or_create_user_from_update(body)
    except Exception as e:
        logger.exception("Webhook get_or_create_user_from_update failed bot_id=%s: %s", bot_id, e)
    try:
        inbound_text = text or (f"[callback:{callback_data}]" if callback_data else "") or (f"[contact:{contact_phone}]" if contact_phone else "")
        if inbound_text:
            TelegramMessageLog.objects.create(
                bot=bot, telegram_user_id=user_id, direction="in", text=inbound_text[:4096], raw_payload=body,
            )
    except Exception as e:
        logger.debug("Message log create failed: %s", e)
    response = engine.process_update(
        session,
        text=text,
        callback_data=callback_data,
        message_id=message_id,
        contact_phone=contact_phone,
        contact_user_id=contact_user_id,
    )
    text_out = response.get("text")
    reply_markup = response.get("reply_markup")
    edit_previous = response.get("edit_previous") and response.get("message_id") is not None
    if callback_query_id:
        try:
            answer_callback_query_via_bot(callback_query_id, bot)
        except Exception as e:
            logger.debug("Webhook answer_callback_query: %s", e)
    if text_out:
        try:
            sent_message_id = None
            if edit_previous:
                ok = edit_message_text_via_bot(
                    chat_id, response["message_id"], text_out, bot, reply_markup=reply_markup,
                )
                if ok:
                    sent_message_id = response["message_id"]
                else:
                    sent_message_id = send_telegram_message_via_bot(chat_id, text_out, bot, reply_markup=reply_markup)
            else:
                sent_message_id = send_telegram_message_via_bot(chat_id, text_out, bot, reply_markup=reply_markup)
            if sent_message_id is not None:
                ctx = session.context or {}
                ctx["last_bot_message_id"] = sent_message_id
                ctx[LAST_PROCESSED_UPDATE_ID_KEY] = update_id
                session.context = ctx
                session.save(update_fields=["context"])
            logger.info(
                "Telegram reply sent bot_id=%s chat_id=%s update_id=%s session_id=%s sent_msg_id=%s edit=%s len=%s",
                bot_id, chat_id, update_id, session.pk, sent_message_id, edit_previous, len(text_out),
            )
        except Exception as e:
            logger.exception("Webhook send/edit failed bot_id=%s chat_id=%s: %s", bot_id, chat_id, e)
            if update_id is not None:
                try:
                    ctx = session.context or {}
                    ctx[LAST_PROCESSED_UPDATE_ID_KEY] = update_id
                    session.context = ctx
                    session.save(update_fields=["context"])
                except Exception:
                    pass
        try:
            TelegramMessageLog.objects.create(
                bot=bot, telegram_user_id=user_id, direction="out", text=text_out[:4096],
                raw_payload={"reply_markup": reply_markup, "edit": edit_previous} if reply_markup or edit_previous else None,
            )
        except Exception as e:
            logger.debug("Message log create failed: %s", e)


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
    TelegramBot.objects.filter(pk=bot.pk).update(last_webhook_received=timezone.now())
    try:
        _process_webhook_payload(bot, body)
    except Exception as e:
        logger.exception("Webhook processing failed bot_id=%s: %s", bot_id, e)
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
    Parse update. Returns (chat_id, text, callback_data, message_id, callback_query_id, contact_phone, contact_user_id).
    contact_user_id: from message.contact.user_id for phone verification.
    """
    message = body.get("message")
    if message:
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        contact = message.get("contact")
        contact_phone = None
        contact_user_id = None
        if contact and contact.get("phone_number"):
            contact_phone = (contact.get("phone_number") or "").strip()
            if contact_phone and not contact_phone.startswith("+"):
                contact_phone = "+" + contact_phone
            try:
                contact_user_id = int(contact.get("user_id")) if contact.get("user_id") is not None else None
            except (TypeError, ValueError):
                pass
        return (chat_id, text or None, None, message.get("message_id"), None, contact_phone or None, contact_user_id)

    edited = body.get("edited_message")
    if edited:
        chat_id = edited.get("chat", {}).get("id")
        text = (edited.get("text") or "").strip()
        return (chat_id, text or None, None, edited.get("message_id"), None, None, None)

    callback = body.get("callback_query")
    if callback:
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        msg = callback.get("message") or {}
        message_id = msg.get("message_id")
        data = (callback.get("data") or "").strip()
        callback_query_id = callback.get("id")
        return (chat_id, None, data or None, message_id, callback_query_id, None, None)

    return (None, None, None, None, None, None, None)
