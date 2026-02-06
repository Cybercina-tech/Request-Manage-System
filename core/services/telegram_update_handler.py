"""
Iraniu — Process one Telegram update (shared by webhook and polling worker).
Takes bot and update dict; runs conversation engine and sends reply.
No rate limit here (webhook applies its own; worker runs single-threaded).
"""

import logging
from core.models import TelegramBot, TelegramMessageLog
from core.services.conversation import ConversationEngine
from core.services import send_telegram_message_via_bot
from core.services.users import get_or_create_user_from_update

logger = logging.getLogger(__name__)


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
    """Returns (chat_id, text, callback_data). chat_id None if nothing to process."""
    message = body.get("message")
    if message:
        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()
        return (chat_id, text or None, None)
    edited = body.get("edited_message")
    if edited:
        chat_id = edited.get("chat", {}).get("id")
        text = (edited.get("text") or "").strip()
        return (chat_id, text or None, None)
    callback = body.get("callback_query")
    if callback:
        chat_id = callback.get("message", {}).get("chat", {}).get("id")
        data = (callback.get("data") or "").strip()
        return (chat_id, None, data or None)
    return (None, None, None)


def process_update(bot: TelegramBot, update: dict) -> None:
    """
    Process a single Telegram update: create/update user, run conversation, send reply.
    Safe to call from webhook or polling worker. Logs errors; does not raise.
    """
    bot_id = bot.pk
    user_id = _get_telegram_user_id(update)
    if not user_id:
        logger.debug("process_update bot_id=%s: no user id", bot_id)
        return
    chat_id, text, callback_data = _parse_update(update)
    if chat_id is None:
        logger.debug("process_update bot_id=%s update_id=%s: no message/callback", bot_id, update.get("update_id"))
        return
    try:
        get_or_create_user_from_update(update)
    except Exception as e:
        logger.exception("process_update get_or_create_user bot_id=%s: %s", bot_id, e)
    try:
        inbound_text = text or (f"[callback:{callback_data}]" if callback_data else "")
        if inbound_text:
            TelegramMessageLog.objects.create(
                bot=bot,
                telegram_user_id=user_id,
                direction="in",
                text=inbound_text[:4096],
                raw_payload=update,
            )
    except Exception as e:
        logger.debug("process_update message log in: %s", e)
    try:
        engine = ConversationEngine(bot)
        session = engine.get_or_create_session(user_id)
        response = engine.process_update(session, text=text, callback_data=callback_data)
    except Exception as e:
        logger.exception("process_update conversation bot_id=%s user_id=%s: %s", bot_id, user_id, e)
        return
    text_out = response.get("text")
    reply_markup = response.get("reply_markup")
    if text_out:
        try:
            send_telegram_message_via_bot(chat_id, text_out, bot, reply_markup=reply_markup)
        except Exception as e:
            logger.exception("process_update send_message bot_id=%s chat_id=%s: %s", bot_id, chat_id, e)
        try:
            TelegramMessageLog.objects.create(
                bot=bot,
                telegram_user_id=user_id,
                direction="out",
                text=text_out[:4096],
                raw_payload={"reply_markup": reply_markup} if reply_markup else None,
            )
        except Exception as e:
            logger.debug("process_update message log out: %s", e)
