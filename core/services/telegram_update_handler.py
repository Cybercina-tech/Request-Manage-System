"""
Iraniu — Process one Telegram update (shared by webhook and polling worker).
Takes bot and update dict; runs conversation engine and sends reply.
Supports inline callbacks (edit previous message), contact sharing, and message logging.

Duplicate prevention: we store last_processed_update_id in session context and skip
any update whose update_id <= that value. This ensures each update is processed at
most once (e.g. when Telegram retries webhook or the same update is delivered twice).
State is always updated inside the engine before returning; we send at most one
response (either edit or send) per update.
"""

import logging
from core.models import TelegramBot, TelegramMessageLog
from core.services.conversation import ConversationEngine
from core.services import (
    send_telegram_message_via_bot,
    edit_message_text_via_bot,
    answer_callback_query_via_bot,
)
from core.services.users import get_or_create_user_from_update

logger = logging.getLogger(__name__)

# Key in session.context for update deduplication (Telegram update_id).
LAST_PROCESSED_UPDATE_ID_KEY = "last_processed_update_id"


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
    Returns (chat_id, text, callback_data, message_id, callback_query_id, contact_phone).
    chat_id None if nothing to process.
    contact_phone: from message.contact.phone_number when user shares contact.
    """
    message = body.get("message")
    if message:
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        contact = message.get("contact")
        contact_phone = None
        if contact and contact.get("phone_number"):
            contact_phone = (contact.get("phone_number") or "").strip()
            if contact_phone and not contact_phone.startswith("+"):
                contact_phone = "+" + contact_phone
        return (chat_id, text or None, None, message.get("message_id"), None, contact_phone or None)

    edited = body.get("edited_message")
    if edited:
        chat_id = edited.get("chat", {}).get("id")
        text = (edited.get("text") or "").strip()
        return (chat_id, text or None, None, edited.get("message_id"), None, None)

    callback = body.get("callback_query")
    if callback:
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        msg = callback.get("message") or {}
        message_id = msg.get("message_id")
        data = (callback.get("data") or "").strip()
        callback_query_id = callback.get("id")
        return (chat_id, None, data or None, message_id, callback_query_id, None)

    return (None, None, None, None, None, None)


def process_update(bot: TelegramBot, update: dict) -> None:
    """
    Process a single Telegram update: at most one outgoing message (or edit) per update.
    Deduplication: skip if update_id was already processed (stored in session context).
    State is updated inside the engine before return; we only send after that.
    """
    bot_id = bot.pk
    update_id = update.get("update_id")
    user_id = _get_telegram_user_id(update)
    if not user_id:
        logger.debug("process_update bot_id=%s update_id=%s: no user id", bot_id, update_id)
        return

    chat_id, text, callback_data, message_id, callback_query_id, contact_phone = _parse_update(update)
    if chat_id is None:
        logger.debug("process_update bot_id=%s update_id=%s: no message/callback", bot_id, update_id)
        return

    engine = ConversationEngine(bot)
    session = engine.get_or_create_session(user_id)
    # Dedup: same update must not trigger a second response (e.g. webhook retry).
    if should_skip_duplicate_update(session, update_id):
        logger.info(
            "process_update skip duplicate update_id=%s session_id=%s bot_id=%s",
            update_id, session.pk, bot_id,
        )
        return

    try:
        get_or_create_user_from_update(update)
    except Exception as e:
        logger.exception("process_update get_or_create_user bot_id=%s: %s", bot_id, e)

    inbound_log = text or (f"[callback:{callback_data}]" if callback_data else "") or (f"[contact:{contact_phone}]" if contact_phone else "")
    try:
        if inbound_log:
            TelegramMessageLog.objects.create(
                bot=bot,
                telegram_user_id=user_id,
                direction="in",
                text=inbound_log[:4096],
                raw_payload=update,
            )
    except Exception as e:
        logger.debug("process_update message log in: %s", e)

    try:
        response = engine.process_update(
            session,
            text=text,
            callback_data=callback_data,
            message_id=message_id,
            contact_phone=contact_phone,
        )
    except Exception as e:
        logger.exception("process_update conversation bot_id=%s user_id=%s update_id=%s: %s", bot_id, user_id, update_id, e)
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
            logger.debug("process_update answer_callback_query: %s", e)

    # Single response rule: either edit or send, never both for the same update.
    # When edit fails (message not editable), we send once as fallback — do not send again.
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
                        "process_update edit failed (message not editable), sending new message once bot_id=%s chat_id=%s",
                        bot_id, chat_id,
                    )
                    sent_message_id = send_telegram_message_via_bot(chat_id, text_out, bot, reply_markup=reply_markup)
            else:
                sent_message_id = send_telegram_message_via_bot(chat_id, text_out, bot, reply_markup=reply_markup)
            logger.info(
                "process_update out update_id=%s session_id=%s bot_id=%s chat_id=%s sent_msg_id=%s edit=%s",
                update_id, session.pk, bot_id, chat_id, sent_message_id, edit_previous,
            )
            if sent_message_id is not None:
                ctx = session.context or {}
                ctx["last_bot_message_id"] = sent_message_id
                ctx[LAST_PROCESSED_UPDATE_ID_KEY] = update_id
                session.context = ctx
                session.save(update_fields=["context"])
            elif sent_message_id is None:
                logger.warning("process_update send failed bot_id=%s chat_id=%s", bot_id, chat_id)
        except Exception as e:
            logger.exception("process_update send_message bot_id=%s chat_id=%s: %s", bot_id, chat_id, e)
        try:
            TelegramMessageLog.objects.create(
                bot=bot,
                telegram_user_id=user_id,
                direction="out",
                text=text_out[:4096],
                raw_payload={"reply_markup": reply_markup, "edit": edit_previous} if reply_markup or edit_previous else None,
            )
        except Exception as e:
            logger.debug("process_update message log out: %s", e)
    # Mark update as processed so duplicate delivery (e.g. webhook retry) is skipped.
    if update_id is not None and (session.context or {}).get(LAST_PROCESSED_UPDATE_ID_KEY) != update_id:
        ctx = session.context or {}
        ctx[LAST_PROCESSED_UPDATE_ID_KEY] = update_id
        session.context = ctx
        session.save(update_fields=["context"])
