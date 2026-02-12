"""
Iraniu — Unified Telegram update dispatcher.

Single entry point for both Webhook and Polling: process_update_payload(bot, update_dict).
Handles de-duplication (update_id), locking (race prevention), routing to ConversationEngine,
and sending the reply. Both the webhook view and the polling worker MUST call this function only.
"""

import logging
import time

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

# ── Stale message tracking ────────────────────────────────────────────
# Telegram does not allow editing messages older than ~48 h, or messages
# that have been deleted / are not from the bot.  When an edit fails we
# store the failed message_id so we never waste another API call on it.
STALE_MESSAGE_IDS_KEY = "stale_message_ids"
LAST_BOT_MSG_TIMESTAMP_KEY = "last_bot_message_ts"
MAX_STALE_IDS = 20  # keep list bounded
MAX_MESSAGE_AGE_SECONDS = 48 * 3600  # Telegram's hard limit for editing


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


def _is_message_stale(session, message_id: int) -> bool:
    """
    Return True if `message_id` was previously marked as non-editable,
    or if the message is older than Telegram's 48-hour edit window.
    """
    if message_id is None:
        return True
    ctx = session.context or {}
    # Check explicit stale list (prior edit failures)
    if message_id in ctx.get(STALE_MESSAGE_IDS_KEY, []):
        return True
    # Check age: if this is the last bot message and it's too old, skip edit
    last_ts = ctx.get(LAST_BOT_MSG_TIMESTAMP_KEY)
    if last_ts and message_id == ctx.get("last_bot_message_id"):
        if time.time() - last_ts > MAX_MESSAGE_AGE_SECONDS:
            return True
    return False


def _mark_message_stale(session, message_id: int) -> None:
    """
    Record `message_id` as non-editable so future callbacks on the same
    message skip the edit attempt entirely (breaking the retry loop).
    NOTE: does NOT call session.save(); the caller must persist.
    """
    if message_id is None:
        return
    ctx = session.context or {}
    stale_ids = ctx.get(STALE_MESSAGE_IDS_KEY, [])
    if message_id not in stale_ids:
        stale_ids.append(message_id)
        # Keep the list bounded to avoid unbounded growth
        if len(stale_ids) > MAX_STALE_IDS:
            stale_ids = stale_ids[-MAX_STALE_IDS:]
        ctx[STALE_MESSAGE_IDS_KEY] = stale_ids
        session.context = ctx


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
    Returns (chat_id, text, callback_data, message_id, callback_query_id, contact_phone, contact_user_id, has_animation, has_sticker).
    chat_id None if nothing to process. has_animation/has_sticker True when message contains GIF or sticker.
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
        has_animation = bool(message.get("animation"))
        has_sticker = bool(message.get("sticker"))
        return (chat_id, text or None, None, message.get("message_id"), None, contact_phone or None, contact_user_id, has_animation, has_sticker)

    edited = body.get("edited_message")
    if edited:
        chat_id = edited.get("chat", {}).get("id")
        text = (edited.get("text") or "").strip()
        has_animation = bool(edited.get("animation"))
        has_sticker = bool(edited.get("sticker"))
        return (chat_id, text or None, None, edited.get("message_id"), None, None, None, has_animation, has_sticker)

    callback = body.get("callback_query")
    if callback:
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        msg = callback.get("message") or {}
        message_id = msg.get("message_id")
        data = (callback.get("data") or "").strip()
        callback_query_id = callback.get("id")
        return (chat_id, None, data or None, message_id, callback_query_id, None, None, False, False)

    return (None, None, None, None, None, None, None, False, False)


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

    chat_id, text, callback_data, message_id, callback_query_id, contact_phone, contact_user_id, has_animation, has_sticker = _parse_update(
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
            has_animation=has_animation,
            has_sticker=has_sticker,
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
    remove_keyboard_first = response.get("remove_keyboard_first")

    if callback_query_id:
        try:
            answer_callback_query_via_bot(callback_query_id, bot)
        except Exception as e:
            logger.debug("process_update_payload answer_callback_query: %s", e)

    sent_message_id = None
    if remove_keyboard_first:
        try:
            first_text = remove_keyboard_first.get("text") or "✅"
            send_telegram_message_via_bot(
                chat_id, first_text, bot, reply_markup={"remove_keyboard": True}
            )
        except Exception as e:
            logger.debug("process_update_payload remove_keyboard_first send: %s", e)

    if text_out:
        try:
            if edit_previous:
                target_msg_id = response["message_id"]

                # ── Stale-message guard: skip edit if we already know it will fail ──
                if _is_message_stale(session, target_msg_id):
                    logger.info(
                        "process_update_payload skipping edit (stale msg) bot_id=%s chat_id=%s msg_id=%s",
                        bot_id, chat_id, target_msg_id,
                    )
                    sent_message_id = send_telegram_message_via_bot(
                        chat_id, text_out, bot, reply_markup=reply_markup
                    )
                else:
                    ok = edit_message_text_via_bot(
                        chat_id,
                        target_msg_id,
                        text_out,
                        bot,
                        reply_markup=reply_markup,
                    )
                    if ok:
                        sent_message_id = target_msg_id
                    else:
                        # Mark this message so we never try to edit it again
                        _mark_message_stale(session, target_msg_id)
                        logger.warning(
                            "process_update_payload edit failed, marked msg_id=%s stale, sending new message bot_id=%s chat_id=%s",
                            target_msg_id, bot_id, chat_id,
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
                ctx[LAST_BOT_MSG_TIMESTAMP_KEY] = time.time()
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
