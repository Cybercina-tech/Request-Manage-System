"""
Iraniu — Centralized conversation state machine (FA/EN).
All logic here; views only parse update and call this.
Handles /start, /start resubmit_<uuid>, and Edit & Resubmit flow.
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

    def process_update(self, session: TelegramSession, text: str | None = None, callback_data: str | None = None) -> dict:
        """
        Process user input. Returns dict with keys: text, reply_markup (optional).
        Updates session state and context; may create AdRequest on SUBMITTED.
        """
        session.last_activity = timezone.now()
        lang = session.language or "en"

        # /start: plain reset or deep link resubmit_<uuid>
        if text and text.strip().lower().startswith("/start"):
            stripped = text.strip()
            if stripped.lower() == "/start":
                session.state = TelegramSession.State.SELECT_LANGUAGE
                session.context = {}
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_select_language(session)
            # /start resubmit_<uuid> (e.g. from t.me/bot?start=resubmit_<uuid>)
            parts = stripped.split(None, 1)
            if len(parts) >= 2 and parts[1].startswith(RESUBMIT_START_PREFIX):
                uuid_str = parts[1][len(RESUBMIT_START_PREFIX) :].strip()
                return self._handle_resubmit_start(session, uuid_str)

        if session.state == TelegramSession.State.START:
            session.state = TelegramSession.State.SELECT_LANGUAGE
            session.save(update_fields=["state", "last_activity"])
            return self._reply_select_language(session)

        if session.state == TelegramSession.State.SELECT_LANGUAGE:
            if callback_data in ("en", "fa") or (text and text.strip().lower() in ("en", "fa", "english", "فارسی")):
                lang = "en" if (callback_data == "en" or (text and text.strip().lower() in ("en", "english"))) else "fa"
                session.language = lang
                session.state = TelegramSession.State.ASK_CONTACT
                session.context = {}
                session.save(update_fields=["language", "state", "context", "last_activity"])
                return self._reply_ask_contact(session)
            return self._reply_select_language(session)

        if session.state == TelegramSession.State.ASK_CONTACT:
            if callback_data == "contact_skip":
                session.state = TelegramSession.State.MAIN_MENU
                session.context = {}
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_main_menu(session)
            if callback_data == "contact_yes":
                session.state = TelegramSession.State.CHOOSE_CONTACT_TYPE
                session.save(update_fields=["state", "last_activity"])
                return self._reply_choose_contact_type(session)
            return self._reply_ask_contact(session)

        if session.state == TelegramSession.State.CHOOSE_CONTACT_TYPE:
            if callback_data == "contact_phone":
                session.state = TelegramSession.State.ENTER_PHONE
                session.save(update_fields=["state", "last_activity"])
                return self._reply_enter_phone(session)
            if callback_data == "contact_email":
                session.state = TelegramSession.State.ENTER_EMAIL
                session.save(update_fields=["state", "last_activity"])
                return self._reply_enter_email(session)
            return self._reply_choose_contact_type(session)

        if session.state == TelegramSession.State.ENTER_PHONE:
            if text and text.strip():
                try:
                    telegram_user = TelegramUser.objects.filter(telegram_user_id=session.telegram_user_id).first()
                    if telegram_user:
                        update_contact_info(telegram_user, phone=text.strip())
                    session.state = TelegramSession.State.MAIN_MENU
                    session.context = {}
                    session.save(update_fields=["state", "context", "last_activity"])
                    return self._reply_contact_saved_then_main_menu(session)
                except ValueError:
                    return self._reply_invalid_phone(session)
            return self._reply_enter_phone(session)

        if session.state == TelegramSession.State.ENTER_EMAIL:
            if text and text.strip():
                try:
                    telegram_user = TelegramUser.objects.filter(telegram_user_id=session.telegram_user_id).first()
                    if telegram_user:
                        update_contact_info(telegram_user, email=text.strip())
                    session.state = TelegramSession.State.MAIN_MENU
                    session.context = {}
                    session.save(update_fields=["state", "context", "last_activity"])
                    return self._reply_contact_saved_then_main_menu(session)
                except ValueError:
                    return self._reply_invalid_email(session)
            return self._reply_enter_email(session)

        if session.state == TelegramSession.State.MAIN_MENU:
            create_key_en = get_message("create_new_ad", "en").lower()
            create_key_fa = get_message("create_new_ad", "fa")
            if callback_data == "create_ad" or (text and (create_key_en in (text or "").lower() or (create_key_fa and create_key_fa in (text or "")))):
                session.state = TelegramSession.State.ENTER_CONTENT
                session.context = {}
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_enter_content(session)
            return self._reply_main_menu(session)

        if session.state == TelegramSession.State.ENTER_CONTENT:
            if not text or not text.strip():
                return self._reply_enter_content(session)
            session.context["content"] = text.strip()[:4000]
            session.state = TelegramSession.State.SELECT_CATEGORY
            session.save(update_fields=["state", "context", "last_activity"])
            return self._reply_select_category(session)

        if session.state == TelegramSession.State.SELECT_CATEGORY:
            cat = callback_data or (text.strip() if text else "")
            valid = [c[0] for c in CATEGORY_KEYS]
            if cat in valid:
                session.context["category"] = cat
                session.state = TelegramSession.State.CONFIRM
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_confirm(session)
            return self._reply_select_category(session)

        if session.state == TelegramSession.State.CONFIRM:
            if callback_data == "confirm_yes":
                ad = self._do_submit(session)
                if ad:
                    session.state = TelegramSession.State.SUBMITTED
                    session.context = {}
                    session.save(update_fields=["state", "context", "last_activity"])
                    return self._reply_submitted(session)
            if callback_data == "confirm_no":
                session.state = TelegramSession.State.MAIN_MENU
                session.context = {}
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_main_menu(session)
            return self._reply_confirm(session)

        if session.state == TelegramSession.State.RESUBMIT_EDIT:
            if text and text.strip():
                session.context["content"] = text.strip()[:4000]
                session.state = TelegramSession.State.RESUBMIT_CONFIRM
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_resubmit_confirm(session)
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
                return self._reply_main_menu(session)
            return self._reply_resubmit_confirm(session)

        # SUBMITTED, EDITING, or unknown: show main menu
        session.state = TelegramSession.State.MAIN_MENU
        session.context = {}
        session.save(update_fields=["state", "context", "last_activity"])
        return self._reply_main_menu(session)

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
        """
        Validate that the ad exists, is rejected, and belongs to this Telegram user.
        Returns (ad, None) if valid, or (None, error_key) for i18n message.
        """
        try:
            ad_uuid = uuid_module.UUID(uuid_str)
        except (ValueError, TypeError):
            logger.info("Resubmit invalid uuid: %s", uuid_str[:50] if uuid_str else "")
            return None, "resubmit_error_not_found"

        ad = AdRequest.objects.filter(uuid=ad_uuid).first()
        if not ad:
            logger.info("Resubmit ad not found: %s", ad_uuid)
            return None, "resubmit_error_not_found"
        if ad.status != AdRequest.Status.REJECTED:
            logger.info("Resubmit ad not rejected: uuid=%s status=%s", ad_uuid, ad.status)
            return None, "resubmit_error_not_rejected"
        if ad.telegram_user_id != telegram_user_id:
            logger.warning("Resubmit user mismatch: uuid=%s ad_user=%s session_user=%s", ad_uuid, ad.telegram_user_id, telegram_user_id)
            return None, "resubmit_error_not_yours"
        return ad, None

    def _handle_resubmit_start(self, session: TelegramSession, uuid_str: str) -> dict | None:
        """
        Handle /start resubmit_<uuid>. Validate ad, set context and state RESUBMIT_EDIT, return reply.
        On validation failure: set session to MAIN_MENU and return error reply (so next message gets main menu).
        """
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
        """
        Submit new ad from resubmit flow; mark original as solved.
        Returns (new_ad, None) on success, or (None, error_response_dict) to send to user.
        """
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
            logger.info("Resubmit: original uuid=%s marked solved; new uuid=%s", ad.uuid, new_ad.uuid)
        return new_ad, None

    def _reply_select_language(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("start", lang)
        reply_markup = {
            "inline_keyboard": [
                [{"text": get_message("lang_en", "en"), "callback_data": "en"}],
                [{"text": get_message("lang_fa", "fa"), "callback_data": "fa"}],
            ]
        }
        return {"text": text, "reply_markup": reply_markup}

    def _reply_ask_contact(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("add_contact_ask", lang)
        reply_markup = {
            "inline_keyboard": [
                [{"text": get_message("add_contact_yes", lang), "callback_data": "contact_yes"}],
                [{"text": get_message("add_contact_skip", lang), "callback_data": "contact_skip"}],
            ]
        }
        return {"text": text, "reply_markup": reply_markup}

    def _reply_choose_contact_type(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("choose_contact_type", lang)
        reply_markup = {
            "inline_keyboard": [
                [{"text": get_message("contact_phone", lang), "callback_data": "contact_phone"}],
                [{"text": get_message("contact_email", lang), "callback_data": "contact_email"}],
            ]
        }
        return {"text": text, "reply_markup": reply_markup}

    def _reply_enter_phone(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("enter_phone", lang)
        return {"text": text}

    def _reply_enter_email(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("enter_email", lang)
        return {"text": text}

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
        text = get_message("invalid_email", lang) + "\n\n" + get_message("enter_email", lang)
        return {"text": text}

    def _reply_main_menu(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("main_menu", lang)
        create_label = get_message("create_new_ad", lang)
        reply_markup = {"inline_keyboard": [[{"text": create_label, "callback_data": "create_ad"}]]}
        return {"text": text, "reply_markup": reply_markup}

    def _reply_enter_content(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("enter_ad_text", lang)
        return {"text": text}

    def _reply_select_category(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("choose_category", lang)
        keyboard = []
        for cat_key, msg_key in CATEGORY_KEYS:
            keyboard.append([{"text": get_message(msg_key, lang), "callback_data": cat_key}])
        return {"text": text, "reply_markup": {"inline_keyboard": keyboard}}

    def _reply_confirm(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("confirm_submission", lang)
        yes_label = "Yes" if lang == "en" else "بله"
        no_label = get_message("cancel", lang)
        reply_markup = {
            "inline_keyboard": [
                [{"text": yes_label, "callback_data": "confirm_yes"}, {"text": no_label, "callback_data": "confirm_no"}]
            ]
        }
        return {"text": text, "reply_markup": reply_markup}

    def _reply_submitted(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("submitted", lang)
        return {"text": text}

    def _reply_resubmit_edit(self, session: TelegramSession, old_content: str = "") -> dict:
        """Show rejected ad content and ask user to send new text."""
        lang = session.language or "en"
        intro = get_message("resubmit_intro", lang)
        prompt = get_message("resubmit_edit_prompt", lang)
        if old_content:
            text = f"{intro}\n\n———\n{old_content[:2000]}\n———\n\n{prompt}"
        else:
            text = f"{intro}\n\n{prompt}"
        return {"text": text}

    def _reply_resubmit_confirm(self, session: TelegramSession) -> dict:
        """Confirm submission of the new version."""
        lang = session.language or "en"
        text = get_message("resubmit_confirm", lang)
        yes_label = "Yes" if lang == "en" else "بله"
        no_label = get_message("cancel", lang)
        reply_markup = {
            "inline_keyboard": [
                [{"text": yes_label, "callback_data": "confirm_yes"}, {"text": no_label, "callback_data": "confirm_no"}]
            ]
        }
        return {"text": text, "reply_markup": reply_markup}

    def _reply_resubmit_success(self, session: TelegramSession) -> dict:
        lang = session.language or "en"
        text = get_message("resubmit_success", lang)
        return {"text": text}

    def _reply_resubmit_error(self, session: TelegramSession, error_key: str) -> dict:
        """Send localized error and suggest main menu."""
        lang = session.language or "en"
        text = get_message(error_key, lang)
        return {"text": text}
