"""
Iraniu — Unified Telegram update dispatcher.

Single entry point for both Webhook and Polling: process_update_payload(bot, update_dict).
Handles de-duplication (update_id), locking (race prevention), routing to ConversationEngine,
and sending the reply. Both the webhook view and the polling worker MUST call this function only.
"""

import logging
from django.core.cache import cache

from core.models import TelegramBot, TelegramMessageLog
from core.services.conversation import ConversationEngine
from core.services import (
    send_telegram_message_via_bot,
    edit_message_text_via_bot,
    answer_callback_query_via_bot,
)
from core.services.users import get_or_create_user_from_update

logger = logging.getLogger(__name__)

# Session context key for update deduplication (Telegram update_id).
LAST_PROCESSED_UPDATE_ID_KEY = "last_processed_update_id"
PROCESSING_LOCK_PREFIX = "telegram_update_lock_"
PROCESSING_LOCK_TIMEOUT = 120  # seconds


def acquire_processing_lock(bot_id: int, update_id: int) -> bool:
    """
    Try to claim this update for processing. Returns True if lock acquired, False if already claimed.
    Prevents same update from being processed by multiple workers or webhook retries.
    """
    if update_id is None:
        return True
    key = f"{PROCESSING_LOCK_PREFIX}{bot_id}_{update_id}"
    return cache.add(key, 1, timeout=PROCESSING_LOCK_TIMEOUT)


def should_skip_duplicate_update(session, update_id: int | None) -> bool:
    """
    Return True if this update was already processed (same or older update_id).
    Enables idempotent handling: same update processed twice produces only one reply.
    """
    if update_id is None:
        return False
    last = (session.context or {}).get(LAST_PROCESSED_UPDATE_ID_KEY)
    if last is None:
        return False
    return update_id <= last


def _get_telegram_user_id(body: dict) -> int:
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
    Returns (chat_id, text, callback_data, message_id, callback_query_id, contact_phone, contact_user_id).
    chat_id None if nothing to process.
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


def process_update_payload(bot: TelegramBot, update_dict: dict) -> None:
    """
    Single entry point for processing one Telegram update. Used by both Webhook view and Polling worker.

    Handles:
    1. De-duplication: skip if update_id was already processed (session context).
    2. Locking: claim update_id in cache to prevent race conditions / duplicate handling.
    3. Routing: ConversationEngine.process_update and sending reply (edit or send).

    Does not raise; logs errors. Caller (webhook view) should always return 200 to Telegram.
    """
    bot_id = bot.pk
    update_id = update_dict.get("update_id")
    user_id = _get_telegram_user_id(update_dict)
    if not user_id:
        logger.debug("process_update_payload bot_id=%s update_id=%s: no user id", bot_id, update_id)
        return

    chat_id, text, callback_data, message_id, callback_query_id, contact_phone, contact_user_id = _parse_update(
        update_dict
    )
    if chat_id is None:
        logger.debug("process_update_payload bot_id=%s update_id=%s: no message/callback", bot_id, update_id)
        return

    if not acquire_processing_lock(bot_id, update_id):
        logger.info("process_update_payload skip (lock held) update_id=%s bot_id=%s", update_id, bot_id)
        return

    engine = ConversationEngine(bot)
    session = engine.get_or_create_session(user_id)
    if should_skip_duplicate_update(session, update_id):
        logger.info(
            "process_update_payload skip duplicate update_id=%s session_id=%s bot_id=%s",
            update_id, session.pk, bot_id,
        )
        return

    try:
        get_or_create_user_from_update(update_dict)
    except Exception as e:
        logger.exception("process_update_payload get_or_create_user bot_id=%s: %s", bot_id, e)

    inbound_log = (
        text
        or (f"[callback:{callback_data}]" if callback_data else "")
        or (f"[contact:{contact_phone}]" if contact_phone else "")
    )
    try:
        if inbound_log:
            TelegramMessageLog.objects.create(
                bot=bot,
                telegram_user_id=user_id,
                direction="in",
                text=inbound_log[:4096],
                raw_payload=update_dict,
            )
    except Exception as e:
        logger.debug("process_update_payload message log in: %s", e)

    try:
        response = engine.process_update(
            session,
            text=text,
            callback_data=callback_data,
            message_id=message_id,
            contact_phone=contact_phone,
            contact_user_id=contact_user_id,
        )
    except Exception as e:
        logger.exception(
            "process_update_payload conversation bot_id=%s user_id=%s update_id=%s: %s",
            bot_id, user_id, update_id, e,
        )
        if callback_query_id:
            try:
                answer_callback_query_via_bot(callback_query_id, bot, text="Error")
            except Exception:
                pass
        return

    text_out = response.get("text")
    reply_markup = response.get("reply_markup")
    edit_previous = response.get("edit_previous") and response.get("message_id") is not None

    if callback_query_id:
        try:
            answer_callback_query_via_bot(callback_query_id, bot)
        except Exception as e:
            logger.debug("process_update_payload answer_callback_query: %s", e)

    sent_message_id = None
    if text_out:
        try:
            if edit_previous:
                ok = edit_message_text_via_bot(
                    chat_id,
                    response["message_id"],
                    text_out,
                    bot,
                    reply_markup=reply_markup,
                )
                if ok:
                    sent_message_id = response["message_id"]
                else:
                    logger.warning(
                        "process_update_payload edit failed, sending new message bot_id=%s chat_id=%s",
                        bot_id, chat_id,
                    )
                    sent_message_id = send_telegram_message_via_bot(
                        chat_id, text_out, bot, reply_markup=reply_markup
                    )
            else:
                sent_message_id = send_telegram_message_via_bot(
                    chat_id, text_out, bot, reply_markup=reply_markup
                )
            logger.info(
                "process_update_payload out update_id=%s session_id=%s bot_id=%s chat_id=%s sent_msg_id=%s edit=%s",
                update_id, session.pk, bot_id, chat_id, sent_message_id, edit_previous,
            )
            if sent_message_id is not None:
                ctx = session.context or {}
                ctx["last_bot_message_id"] = sent_message_id
                ctx[LAST_PROCESSED_UPDATE_ID_KEY] = update_id
                session.context = ctx
                session.save(update_fields=["context"])
            else:
                logger.warning("process_update_payload send failed bot_id=%s chat_id=%s", bot_id, chat_id)
        except Exception as e:
            logger.exception(
                "process_update_payload send/edit failed bot_id=%s chat_id=%s: %s", bot_id, chat_id, e
            )
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
                bot=bot,
                telegram_user_id=user_id,
                direction="out",
                text=text_out[:4096],
                raw_payload={
                    "reply_markup": reply_markup,
                    "edit": edit_previous,
                } if reply_markup or edit_previous else None,
            )
        except Exception as e:
            logger.debug("process_update_payload message log out: %s", e)

    if update_id is not None and (session.context or {}).get(LAST_PROCESSED_UPDATE_ID_KEY) != update_id:
        ctx = session.context or {}
        ctx[LAST_PROCESSED_UPDATE_ID_KEY] = update_id
        session.context = ctx
        session.save(update_fields=["context"])
