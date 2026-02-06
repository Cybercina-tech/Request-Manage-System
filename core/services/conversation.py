"""
Iraniu — Centralized conversation state machine (FA/EN).
All logic here; views only parse update and call this.
Handles /start, /start resubmit_<uuid>, inline buttons, edit previous message, contact at end.
"""

import logging
import uuid as uuid_module
from django.utils import timezone

from core.i18n import get_message
from core.models import TelegramSession, TelegramBot, AdRequest, TelegramUser
from core.services.submit_ad_service import SubmitAdService
from core.services.users import update_contact_info

logger = logging.getLogger(__name__)

# Deep link prefix for Edit & Resubmit (matches t.me/bot?start=resubmit_<uuid>)
RESUBMIT_START_PREFIX = "resubmit_"

# Valid categories for keyboard
CATEGORY_KEYS = [
    ("job_vacancy", "category_job"),
    ("rent", "category_rent"),
    ("events", "category_events"),
    ("services", "category_services"),
    ("sale", "category_sale"),
    ("other", "category_other"),
]


class ConversationEngine:
    """
    State machine for Telegram conversation.
    No business logic in views; all transitions and responses here.
    Returns dict with text, reply_markup, and optionally edit_previous, message_id.
    """

    def __init__(self, bot: TelegramBot):
        self.bot = bot

    def get_or_create_session(self, telegram_user_id: int) -> TelegramSession:
        session, _ = TelegramSession.objects.get_or_create(
            telegram_user_id=telegram_user_id,
            bot=self.bot,
            defaults={"state": TelegramSession.State.START},
        )
        return session

    def process_update(
        self,
        session: TelegramSession,
        text: str | None = None,
        callback_data: str | None = None,
        message_id: int | None = None,
        contact_phone: str | None = None,
    ) -> dict:
        """
        Process user input. Returns dict with keys: text, reply_markup (optional),
        edit_previous (bool), message_id (for editing). May include reply_keyboard
        for request_contact. Updates session state and context; may create AdRequest on SUBMITTED.
        """
        session.last_activity = timezone.now()
        lang = session.language or "en"
        # When we have a callback, we prefer to edit the same message
        edit_previous = message_id is not None

        # /start: plain reset or deep link resubmit_<uuid>
        if text and text.strip().lower().startswith("/start"):
            stripped = text.strip()
            if stripped.lower() == "/start":
                session.state = TelegramSession.State.SELECT_LANGUAGE
                session.context = {}
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_select_language(session, edit_previous, message_id)
            parts = stripped.split(None, 1)
            if len(parts) >= 2 and parts[1].startswith(RESUBMIT_START_PREFIX):
                uuid_str = parts[1][len(RESUBMIT_START_PREFIX) :].strip()
                return self._handle_resubmit_start(session, uuid_str)

        if session.state == TelegramSession.State.START:
            session.state = TelegramSession.State.SELECT_LANGUAGE
            session.save(update_fields=["state", "last_activity"])
            return self._reply_select_language(session, edit_previous, message_id)

        if session.state == TelegramSession.State.SELECT_LANGUAGE:
            if callback_data in ("en", "fa") or (text and text.strip().lower() in ("en", "fa", "english", "فارسی")):
                lang = "en" if (callback_data == "en" or (text and text.strip().lower() in ("en", "english"))) else "fa"
                session.language = lang
                session.state = TelegramSession.State.MAIN_MENU
                session.context = {}
                session.save(update_fields=["language", "state", "context", "last_activity"])
                return self._reply_main_menu(session, edit_previous, message_id)
            return self._reply_select_language(session, edit_previous, message_id)

        if session.state == TelegramSession.State.MAIN_MENU:
            create_key_en = get_message("create_new_ad", "en").lower()
            create_key_fa = get_message("create_new_ad", "fa")
            if callback_data == "create_ad" or (
                text and (create_key_en in (text or "").lower() or (create_key_fa and create_key_fa in (text or "")))
            ):
                session.state = TelegramSession.State.ENTER_CONTENT
                session.context = {}
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_enter_content(session)
            return self._reply_main_menu(session, edit_previous, message_id)

        if session.state == TelegramSession.State.ENTER_CONTENT:
            if not text or not text.strip():
                return self._reply_enter_content(session)
            session.context["content"] = text.strip()[:4000]
            session.state = TelegramSession.State.SELECT_CATEGORY
            session.save(update_fields=["state", "context", "last_activity"])
            return self._reply_select_category(session, edit_previous, message_id)

        if session.state == TelegramSession.State.SELECT_CATEGORY:
            cat = callback_data or (text.strip() if text else "")
            valid = [c[0] for c in CATEGORY_KEYS]
            if cat in valid:
                session.context["category"] = cat
                session.state = TelegramSession.State.CONFIRM
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_confirm(session, edit_previous, message_id)
            return self._reply_select_category(session, edit_previous, message_id)

        if session.state == TelegramSession.State.CONFIRM:
            if callback_data == "confirm_yes":
                session.state = TelegramSession.State.ASK_CONTACT
                session.save(update_fields=["state", "last_activity"])
                return self._reply_ask_contact(session)
            if callback_data == "confirm_no":
                session.state = TelegramSession.State.MAIN_MENU
                session.context = {}
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_main_menu(session, edit_previous, message_id)
            if callback_data == "confirm_back":
                session.state = TelegramSession.State.SELECT_CATEGORY
                session.save(update_fields=["state", "last_activity"])
                return self._reply_select_category(session, edit_previous, message_id)
            if callback_data == "confirm_edit":
                session.state = TelegramSession.State.ENTER_CONTENT
                session.save(update_fields=["state", "last_activity"])
                return self._reply_enter_content(session, old_content=session.context.get("content", ""))
            return self._reply_confirm(session, edit_previous, message_id)

        if session.state == TelegramSession.State.ASK_CONTACT:
            if contact_phone:
                try:
                    telegram_user = TelegramUser.objects.filter(telegram_user_id=session.telegram_user_id).first()
                    if telegram_user:
                        update_contact_info(telegram_user, phone=contact_phone, mark_phone_verified=True)
                    session.state = TelegramSession.State.ENTER_EMAIL
                    session.save(update_fields=["state", "last_activity"])
                    return self._reply_ask_email(session, after_contact=True)
                except ValueError:
                    return self._reply_invalid_phone(session)
            # Skip: text "skip" (reply keyboard has no inline skip; user types skip)
            skip_texts = ("skip", "رد کردن", "skip email", "رد")
            if text and text.strip().lower() in skip_texts or (callback_data == "contact_skip"):
                session.state = TelegramSession.State.ENTER_EMAIL
                session.save(update_fields=["state", "last_activity"])
                return self._reply_ask_email(session, after_contact=False)
            return self._reply_ask_contact(session)

        if session.state == TelegramSession.State.ENTER_EMAIL:
            if callback_data == "email_skip":
                ad = self._do_submit(session)
                if ad:
                    session.state = TelegramSession.State.SUBMITTED
                    session.context = {}
                    session.save(update_fields=["state", "context", "last_activity"])
                    return self._reply_submitted(session)
                return self._reply_error_generic(session)
            if text and text.strip():
                try:
                    telegram_user = TelegramUser.objects.filter(telegram_user_id=session.telegram_user_id).first()
                    if telegram_user:
                        update_contact_info(telegram_user, email=text.strip())
                    ad = self._do_submit(session)
                    if ad:
                        session.state = TelegramSession.State.SUBMITTED
                        session.context = {}
                        session.save(update_fields=["state", "context", "last_activity"])
                        return self._reply_submitted(session)
                    return self._reply_error_generic(session)
                except ValueError:
                    return self._reply_invalid_email(session)
            # Re-prompt for email or skip (e.g. after invalid email)
            return self._reply_ask_email(session, after_contact=False)

        if session.state == TelegramSession.State.RESUBMIT_EDIT:
            if text and text.strip():
                session.context["content"] = text.strip()[:4000]
                session.state = TelegramSession.State.RESUBMIT_CONFIRM
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_resubmit_confirm(session, edit_previous, message_id)
            old_content = (session.context or {}).get("original_content", "")
            return self._reply_resubmit_edit(session, old_content=old_content)

        if session.state == TelegramSession.State.RESUBMIT_CONFIRM:
            if callback_data == "confirm_yes":
                ad, error_response = self._do_resubmit(session)
                if error_response is not None:
                    return error_response
                if ad:
                    session.state = TelegramSession.State.SUBMITTED
                    session.context = {}
                    session.save(update_fields=["state", "context", "last_activity"])
                    return self._reply_resubmit_success(session)
            if callback_data == "confirm_no":
                session.state = TelegramSession.State.MAIN_MENU
                session.context = {}
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_main_menu(session, edit_previous, message_id)
            return self._reply_resubmit_confirm(session, edit_previous, message_id)

        # SUBMITTED, EDITING, or unknown: show main menu
        session.state = TelegramSession.State.MAIN_MENU
        session.context = {}
        session.save(update_fields=["state", "context", "last_activity"])
        return self._reply_main_menu(session, edit_previous, message_id)

    def _do_submit(self, session: TelegramSession) -> AdRequest | None:
        content = (session.context or {}).get("content")
        category = (session.context or {}).get("category", AdRequest.Category.OTHER)
        if not content:
            return None
        telegram_user = TelegramUser.objects.filter(telegram_user_id=session.telegram_user_id).first()
        contact_snapshot = {}
        if telegram_user:
            contact_snapshot = {
                "phone": telegram_user.phone_number or "",
                "email": telegram_user.email or "",
                "verified_phone": telegram_user.phone_verified,
                "verified_email": telegram_user.email_verified,
            }
        return SubmitAdService.submit(
            content=content,
            category=category,
            telegram_user_id=session.telegram_user_id,
            telegram_username=getattr(telegram_user, "username", None) if telegram_user else None,
            bot=session.bot,
            raw_telegram_json=None,
            user=telegram_user,
            contact_snapshot=contact_snapshot,
        )

    def _validate_resubmit_ad(self, uuid_str: str, telegram_user_id: int) -> tuple[AdRequest | None, str | None]:
        try:
            ad_uuid = uuid_module.UUID(uuid_str)
        except (ValueError, TypeError):
            logger.info("Resubmit invalid uuid: %s", uuid_str[:50] if uuid_str else "")
            return None, "resubmit_error_not_found"

        ad = AdRequest.objects.filter(uuid=ad_uuid).first()
        if not ad:
            return None, "resubmit_error_not_found"
        if ad.status != AdRequest.Status.REJECTED:
            return None, "resubmit_error_not_rejected"
        if ad.telegram_user_id != telegram_user_id:
            return None, "resubmit_error_not_yours"
        return ad, None

    def _handle_resubmit_start(self, session: TelegramSession, uuid_str: str) -> dict | None:
        ad, error_key = self._validate_resubmit_ad(uuid_str, session.telegram_user_id)
        if error_key is not None:
            session.context = {}
            session.state = TelegramSession.State.MAIN_MENU
            session.save(update_fields=["state", "context", "last_activity"])
            return self._reply_resubmit_error(session, error_key)

        session.context = {
            "mode": "resubmit",
            "original_ad_id": str(ad.uuid),
            "original_category": ad.category,
            "original_content": ad.content or "",
        }
        session.state = TelegramSession.State.RESUBMIT_EDIT
        session.save(update_fields=["state", "context", "last_activity"])
        return self._reply_resubmit_edit(session, old_content=ad.content)

    def _do_resubmit(self, session: TelegramSession) -> tuple[AdRequest | None, dict | None]:
        ctx = session.context or {}
        original_ad_id = ctx.get("original_ad_id")
        content = (ctx.get("content") or "").strip()
        category = ctx.get("original_category") or AdRequest.Category.OTHER

        if not original_ad_id or not content:
            lang = session.language or "en"
            return None, {"text": get_message("resubmit_error_not_found", lang)}

        ad, error_key = self._validate_resubmit_ad(original_ad_id, session.telegram_user_id)
        if error_key is not None:
            return None, self._reply_resubmit_error(session, error_key)

        telegram_user = TelegramUser.objects.filter(telegram_user_id=session.telegram_user_id).first()
        contact_snapshot = {}
        if telegram_user:
            contact_snapshot = {
                "phone": telegram_user.phone_number or "",
                "email": telegram_user.email or "",
                "verified_phone": telegram_user.phone_verified,
                "verified_email": telegram_user.email_verified,
            }

        new_ad = SubmitAdService.submit(
            content=content,
            category=category,
            telegram_user_id=session.telegram_user_id,
            telegram_username=getattr(telegram_user, "username", None) if telegram_user else None,
            bot=session.bot,
            raw_telegram_json=None,
            user=telegram_user,
            contact_snapshot=contact_snapshot,
        )
        if new_ad:
            ad.status = AdRequest.Status.SOLVED
            ad.save(update_fields=["status"])
        return new_ad, None

    def _reply_select_language(self, session: TelegramSession, edit_previous: bool = False, message_id: int | None = None) -> dict:
        lang = session.language or "en"
        text = get_message("start", lang)
        reply_markup = {
            "inline_keyboard": [
                [{"text": get_message("lang_fa", "fa"), "callback_data": "fa"}, {"text": get_message("lang_en", "en"), "callback_data": "en"}],
            ]
        }
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_main_menu(self, session: TelegramSession, edit_previous: bool = False, message_id: int | None = None) -> dict:
        lang = session.language or "en"
        text = get_message("main_menu", lang)
        create_label = get_message("create_new_ad", lang)
        reply_markup = {"inline_keyboard": [[{"text": create_label, "callback_data": "create_ad"}]]}
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_enter_content(self, session: TelegramSession, old_content: str = "") -> dict:
        lang = session.language or "en"
        text = get_message("enter_ad_text", lang)
        if old_content:
            text = f"{text}\n\n———\n{old_content[:500]}\n———"
        return {"text": text}

    def _reply_select_category(self, session: TelegramSession, edit_previous: bool = False, message_id: int | None = None) -> dict:
        lang = session.language or "en"
        text = get_message("choose_category", lang)
        keyboard = []
        for cat_key, msg_key in CATEGORY_KEYS:
            keyboard.append([{"text": get_message(msg_key, lang), "callback_data": cat_key}])
        out = {"text": text, "reply_markup": {"inline_keyboard": keyboard}}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_confirm(self, session: TelegramSession, edit_previous: bool = False, message_id: int | None = None) -> dict:
        lang = session.language or "en"
        content = (session.context or {}).get("content", "")
        category = (session.context or {}).get("category", "other")
        cat_label = next((get_message(msg_key, lang) for ck, msg_key in CATEGORY_KEYS if ck == category), category)
        preview = content[:600] + ("…" if len(content) > 600 else "")
        text = (
            f"{get_message('confirm_submission', lang)}\n\n"
            f"{get_message('content_confirm', lang)}\n{preview}\n\n"
            f"{get_message('category_confirm', lang)} {cat_label}"
        )
        yes_label = get_message("confirm_yes_btn", lang)
        no_label = get_message("cancel", lang)
        back_label = get_message("back", lang)
        edit_label = get_message("edit_btn", lang)
        reply_markup = {
            "inline_keyboard": [
                [{"text": yes_label, "callback_data": "confirm_yes"}, {"text": no_label, "callback_data": "confirm_no"}],
                [{"text": back_label, "callback_data": "confirm_back"}, {"text": edit_label, "callback_data": "confirm_edit"}],
            ]
        }
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_ask_contact(self, session: TelegramSession) -> dict:
        """Ask for phone via Telegram contact share. reply_keyboard = ReplyKeyboardMarkup with request_contact."""
        lang = session.language or "en"
        text = get_message("ask_contact", lang)
        reply_markup = {
            "keyboard": [[{"text": get_message("share_contact_btn", lang), "request_contact": True}]],
            "one_time_keyboard": True,
            "resize_keyboard": True,
        }
        return {"text": text, "reply_markup": reply_markup}

    def _reply_ask_email(self, session: TelegramSession, after_contact: bool = False) -> dict:
        lang = session.language or "en"
        if after_contact:
            text = get_message("contact_received", lang)
        else:
            text = get_message("ask_email", lang)
        skip_label = get_message("email_skip", lang)
        reply_markup = {"inline_keyboard": [[{"text": skip_label, "callback_data": "email_skip"}]]}
        return {"text": text, "reply_markup": reply_markup}

    def _reply_contact_saved_then_main_menu(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        saved = get_message("contact_saved", lang)
        menu_text = get_message("main_menu", lang)
        create_label = get_message("create_new_ad", lang)
        text = f"{saved}\n\n{menu_text}"
        reply_markup = {"inline_keyboard": [[{"text": create_label, "callback_data": "create_ad"}]]}
        return {"text": text, "reply_markup": reply_markup}

    def _reply_invalid_phone(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("invalid_phone", lang) + "\n\n" + get_message("enter_phone", lang)
        return {"text": text}

    def _reply_invalid_email(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("invalid_email", lang) + "\n\n" + get_message("ask_email", lang)
        return {"text": text}

    def _reply_submitted(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("submitted", lang) + "\n\n" + get_message("thank_you_emoji", lang)
        return {"text": text}

    def _reply_error_generic(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("error_generic", lang)
        return {"text": text}

    def _reply_resubmit_edit(self, session: TelegramSession, old_content: str = "") -> dict:
        lang = session.language or "en"
        intro = get_message("resubmit_intro", lang)
        prompt = get_message("resubmit_edit_prompt", lang)
        if old_content:
            text = f"{intro}\n\n———\n{old_content[:2000]}\n———\n\n{prompt}"
        else:
            text = f"{intro}\n\n{prompt}"
        return {"text": text}

    def _reply_resubmit_confirm(self, session: TelegramSession, edit_previous: bool = False, message_id: int | None = None) -> dict:
        lang = session.language or "en"
        text = get_message("resubmit_confirm", lang)
        yes_label = get_message("confirm_yes_btn", lang)
        no_label = get_message("cancel", lang)
        reply_markup = {
            "inline_keyboard": [
                [{"text": yes_label, "callback_data": "confirm_yes"}, {"text": no_label, "callback_data": "confirm_no"}]
            ]
        }
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_resubmit_success(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("resubmit_success", lang) + "\n\n" + get_message("thank_you_emoji", lang)
        return {"text": text}

    def _reply_resubmit_error(self, session: TelegramSession, error_key: str) -> dict:
        lang = session.language or "en"
        text = get_message(error_key, lang)
        create_label = get_message("create_new_ad", lang)
        reply_markup = {"inline_keyboard": [[{"text": create_label, "callback_data": "create_ad"}]]}
        return {"text": text, "reply_markup": reply_markup}
