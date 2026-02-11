"""
Iraniu — Centralized conversation state machine (FA/EN).
All logic here; views only parse update and call this.
Handles /start, /start resubmit_<uuid>, inline buttons, edit previous message, contact at end.
"""

import logging
import uuid as uuid_module
from django.utils import timezone

try:
    import emoji
except ImportError:
    emoji = None

from django.urls import reverse

from core.i18n import get_message
from core.models import TelegramSession, TelegramBot, AdRequest, Category, TelegramUser, SiteConfiguration
from core.services.submit_ad_service import SubmitAdService
from core.services.users import update_contact_info

logger = logging.getLogger(__name__)

# Deep link prefix for Edit & Resubmit (matches t.me/bot?start=resubmit_<uuid>)
RESUBMIT_START_PREFIX = "resubmit_"

# Fallback i18n keys for legacy/default categories (when Category model not yet migrated)
_CATEGORY_I18N_KEYS = {
    "job_vacancy": "category_job",
    "rent": "category_rent",
    "events": "category_events",
    "services": "category_services",
    "sale": "category_sale",
    "other": "category_other",
}


def _get_active_categories():
    """Return active categories for bot keyboard (ordered)."""
    return list(Category.objects.filter(is_active=True).order_by("order", "name"))


def _get_category_display(cat_slug: str, lang: str | None) -> str:
    """Display name for category: from model first, else i18n fallback."""
    cat = Category.objects.filter(slug=cat_slug, is_active=True).first()
    if cat:
        return cat.name
    key = _CATEGORY_I18N_KEYS.get(cat_slug, "category_other")
    return get_message(key, lang)


def contains_emoji(text: str) -> bool:
    """Return True if the string contains any emoji. Uses the emoji library if available."""
    if not text or not isinstance(text, str):
        return False
    if emoji is None:
        return False
    return bool(emoji.emoji_list(text))


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
        contact_user_id: int | None = None,
        has_animation: bool = False,
        has_sticker: bool = False,
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

        # /status: admin check (no state change)
        if text and text.strip().lower() == "/status":
            from core.models import AdminProfile
            is_admin = AdminProfile.objects.filter(telegram_id=str(session.telegram_user_id)).exists()
            msg = "✅ شما مدیر فعال هستید." if is_admin else "❌ شما دسترسی مدیریت ندارید."
            return {"text": msg, "reply_markup": None, "edit_previous": edit_previous, "message_id": message_id}

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

        # Flow: MAIN_MENU → (about_us | my_ads | create_ad) → SELECT_CATEGORY → ENTER_CONTENT → CONFIRM
        if session.state == TelegramSession.State.MAIN_MENU:
            if callback_data == "back_to_home":
                return self._reply_main_menu(session, edit_previous, message_id)
            if callback_data == "about_us":
                return self._reply_about_us(session, edit_previous, message_id)
            my_ads_en = (get_message("btn_my_ads", "en") or "").strip()
            my_ads_fa = (get_message("btn_my_ads", "fa") or "").strip()
            if callback_data == "my_ads" or (text and (text.strip() == my_ads_en or text.strip() == my_ads_fa)):
                session.state = TelegramSession.State.MY_ADS
                session.save(update_fields=["state", "last_activity"])
                logger.info("conversation state session_id=%s MAIN_MENU → MY_ADS", session.pk)
                return self._reply_my_ads(session, edit_previous, message_id)
            create_key_en = get_message("create_new_ad", "en").lower()
            create_key_fa = get_message("create_new_ad", "fa")
            if callback_data == "create_ad" or (
                text and (create_key_en in (text or "").lower() or (create_key_fa and create_key_fa in (text or "")))
            ):
                telegram_user = TelegramUser.objects.filter(telegram_user_id=session.telegram_user_id).first()
                if telegram_user and telegram_user.phone_verified:
                    session.state = TelegramSession.State.SELECT_CATEGORY
                    session.context = {}
                    session.save(update_fields=["state", "context", "last_activity"])
                    logger.info("conversation state session_id=%s MAIN_MENU → SELECT_CATEGORY", session.pk)
                    return self._reply_select_category(session, edit_previous, message_id)
                session.state = TelegramSession.State.ASK_CONTACT
                session.context = {}
                session.save(update_fields=["state", "context", "last_activity"])
                logger.info("conversation state session_id=%s MAIN_MENU → ASK_CONTACT (phone not verified)", session.pk)
                return self._reply_ask_contact(session)
            return self._reply_main_menu(session, edit_previous, message_id)

        if session.state == TelegramSession.State.MY_ADS:
            if callback_data == "back_to_home":
                session.state = TelegramSession.State.MAIN_MENU
                session.save(update_fields=["state", "last_activity"])
                return self._reply_main_menu(session, edit_previous, message_id)
            if callback_data == "list_ads":
                return self._reply_my_ads(session, edit_previous, message_id)
            if callback_data and callback_data.startswith("manage_ad:"):
                try:
                    ad_id = int(callback_data.split(":", 1)[1])
                except (ValueError, IndexError):
                    return self._reply_my_ads(session, edit_previous, message_id)
                return self._reply_ad_detail(session, ad_id, edit_previous, message_id)
            if callback_data and callback_data.startswith("delete_ad:"):
                try:
                    ad_id = int(callback_data.split(":", 1)[1])
                except (ValueError, IndexError):
                    return self._reply_my_ads(session, edit_previous, message_id)
                return self._reply_delete_confirm(session, ad_id, edit_previous, message_id)
            if callback_data and callback_data.startswith("confirm_delete:yes:"):
                try:
                    ad_id = int(callback_data.split(":", 2)[2])
                except (ValueError, IndexError):
                    return self._reply_my_ads(session, edit_previous, message_id)
                ad = self._get_user_ad(session, ad_id)
                if ad:
                    try:
                        ad.delete()
                    except Exception as e:
                        logger.exception("delete ad pk=%s: %s", ad_id, e)
                lang = session.language or "en"
                deleted_msg = get_message("ad_deleted", lang)
                out = self._reply_my_ads(session, edit_previous, message_id)
                out["text"] = deleted_msg + "\n\n" + (out.get("text") or "")
                return out
            if callback_data and callback_data.startswith("confirm_delete:no:"):
                try:
                    ad_id = int(callback_data.split(":", 2)[2])
                except (ValueError, IndexError):
                    return self._reply_my_ads(session, edit_previous, message_id)
                return self._reply_ad_detail(session, ad_id, edit_previous, message_id)
            if callback_data and callback_data.startswith("edit_ad:"):
                try:
                    ad_id = int(callback_data.split(":", 1)[1])
                except (ValueError, IndexError):
                    return self._reply_my_ads(session, edit_previous, message_id)
                ad = self._get_user_ad(session, ad_id)
                lang = session.language or "en"
                if not ad:
                    text = get_message("ad_not_found", lang)
                    reply_markup = {"inline_keyboard": [[{"text": get_message("btn_back_to_list", lang), "callback_data": "list_ads"}]]}
                    return {"text": text, "reply_markup": reply_markup, "edit_previous": edit_previous, "message_id": message_id}
                config = SiteConfiguration.get_config()
                base = (config.production_base_url or "").strip().rstrip("/")
                if base:
                    path = reverse("request_detail", kwargs={"uuid": ad.uuid})
                    url = base + path
                else:
                    url = "(Set production_base_url in Settings)"
                text = get_message("edit_ad_link_msg", lang).format(url=url)
                back_btn = get_message("btn_back_to_list", lang)
                reply_markup = {"inline_keyboard": [[{"text": back_btn, "callback_data": "list_ads"}]]}
                out = {"text": text, "reply_markup": reply_markup}
                if edit_previous and message_id is not None:
                    out["edit_previous"] = True
                    out["message_id"] = message_id
                return out
            return self._reply_my_ads(session, edit_previous, message_id)

        if session.state == TelegramSession.State.SELECT_CATEGORY:
            if callback_data == "back_to_home":
                session.state = TelegramSession.State.MAIN_MENU
                session.save(update_fields=["state", "last_activity"])
                return self._reply_main_menu(session, edit_previous, message_id)
            # State lock: ignore stale "create_ad" callback — prevents duplicate "First choose a category"
            if callback_data == "create_ad":
                return {"text": ""}
            cat = callback_data or (text.strip() if text else "")
            valid = [c.slug for c in _get_active_categories()]
            if cat in valid:
                session.context["category"] = cat
                session.state = TelegramSession.State.ENTER_CONTENT
                session.save(update_fields=["state", "context", "last_activity"])
                logger.info("conversation state session_id=%s SELECT_CATEGORY → ENTER_CONTENT category=%s", session.pk, cat)
                return self._reply_after_category(session, edit_previous, message_id)
            return self._reply_select_category(session, edit_previous, message_id)

        if session.state == TelegramSession.State.ENTER_CONTENT:
            if callback_data == "back_to_home":
                session.state = TelegramSession.State.MAIN_MENU
                session.context = {}
                session.save(update_fields=["state", "context", "last_activity"])
                return self._reply_main_menu(session, edit_previous, message_id)
            if has_animation or has_sticker:
                return self._reply_ad_content_validation_error(session, edit_previous, message_id, state_enter_content=True)
            if not text or not text.strip():
                return self._reply_enter_content(session, edit_previous=edit_previous, message_id=message_id)
            if contains_emoji(text):
                return self._reply_ad_content_validation_error(session, edit_previous, message_id, state_enter_content=True)
            session.context["content"] = text.strip()[:4000]
            session.state = TelegramSession.State.CONFIRM
            session.save(update_fields=["state", "context", "last_activity"])
            logger.info("conversation state session_id=%s ENTER_CONTENT → CONFIRM", session.pk)
            return self._reply_confirm(session, edit_previous, message_id)

        if session.state == TelegramSession.State.CONFIRM:
            if callback_data == "confirm_yes":
                session.state = TelegramSession.State.ENTER_EMAIL
                session.save(update_fields=["state", "last_activity"])
                return self._reply_ask_email(session, after_contact=False)
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
                return self._reply_enter_content(
                    session,
                    old_content=session.context.get("content", ""),
                    edit_previous=edit_previous,
                    message_id=message_id,
                )
            return self._reply_confirm(session, edit_previous, message_id)

        if session.state == TelegramSession.State.ASK_CONTACT:
            if contact_phone:
                # Verify contact belongs to the user (contact.user_id must match message.from_user.id)
                if contact_user_id is None or int(contact_user_id) != int(session.telegram_user_id):
                    logger.warning("ASK_CONTACT: contact user_id=%s does not match session user_id=%s", contact_user_id, session.telegram_user_id)
                    return self._reply_contact_not_verified(session)
                try:
                    telegram_user = TelegramUser.objects.filter(telegram_user_id=session.telegram_user_id).first()
                    if telegram_user:
                        update_contact_info(telegram_user, phone=contact_phone, mark_phone_verified=True)
                    session.state = TelegramSession.State.SELECT_CATEGORY
                    session.save(update_fields=["state", "last_activity"])
                    logger.info("conversation state session_id=%s ASK_CONTACT → SELECT_CATEGORY (phone verified)", session.pk)
                    out = self._reply_select_category(session, edit_previous, message_id)
                    out["remove_keyboard_first"] = {"text": get_message("phone_number_saved", session.language or "en")}
                    return out
                except ValueError:
                    return self._reply_invalid_phone(session)
            if text and text.strip():
                return self._reply_ask_contact_use_button(session)
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
            # Re-prompt: invalid email shows short message; empty shows full prompt with Skip button
            return self._reply_ask_email(session, after_contact=False)

        if session.state == TelegramSession.State.RESUBMIT_EDIT:
            if has_animation or has_sticker:
                return self._reply_ad_content_validation_error(session, edit_previous, message_id, state_enter_content=False)
            if text and text.strip():
                if contains_emoji(text):
                    return self._reply_ad_content_validation_error(session, edit_previous, message_id, state_enter_content=False)
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
        category = (session.context or {}).get("category", "other")
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
        if ad.status not in (AdRequest.Status.REJECTED, AdRequest.Status.NEEDS_REVISION):
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
            "original_category": ad.category.slug if ad.category else "other",
            "original_content": ad.content or "",
        }
        session.state = TelegramSession.State.RESUBMIT_EDIT
        session.save(update_fields=["state", "context", "last_activity"])
        return self._reply_resubmit_edit(session, old_content=ad.content)

    def _do_resubmit(self, session: TelegramSession) -> tuple[AdRequest | None, dict | None]:
        ctx = session.context or {}
        original_ad_id = ctx.get("original_ad_id")
        content = (ctx.get("content") or "").strip()
        category = ctx.get("original_category") or "other"

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
        text = get_message("main_menu_greeting", lang)
        about_label = get_message("btn_about_us", lang)
        my_ads_label = get_message("btn_my_ads", lang)
        create_label = get_message("create_new_ad", lang)
        reply_markup = {
            "inline_keyboard": [
                [{"text": about_label, "callback_data": "about_us"}, {"text": my_ads_label, "callback_data": "my_ads"}],
                [{"text": create_label, "callback_data": "create_ad"}],
            ]
        }
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_about_us(self, session: TelegramSession, edit_previous: bool = False, message_id: int | None = None) -> dict:
        lang = session.language or "en"
        text = get_message("about_us_message", lang)
        back_label = get_message("btn_back_to_home", lang)
        reply_markup = {"inline_keyboard": [[{"text": back_label, "callback_data": "back_to_home"}]]}
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _get_user_ad(self, session: TelegramSession, ad_id: int):
        """Return AdRequest if it belongs to this user (telegram_user_id), else None. Security: owner only."""
        try:
            ad = AdRequest.objects.filter(
                pk=ad_id,
                telegram_user_id=session.telegram_user_id,
            ).first()
            return ad
        except (ValueError, TypeError):
            return None

    def _reply_my_ads(self, session: TelegramSession, edit_previous: bool = False, message_id: int | None = None) -> dict:
        lang = session.language or "en"
        try:
            ads = list(
                AdRequest.objects.filter(telegram_user_id=session.telegram_user_id).order_by("-created_at")[:20]
            )
        except Exception as e:
            logger.exception("_reply_my_ads query failed: %s", e)
            ads = []
        intro = get_message("my_ads_intro", lang)
        if not ads:
            text = get_message("my_ads_empty", lang)
            keyboard = [[{"text": get_message("btn_back_to_home", lang), "callback_data": "back_to_home"}]]
        else:
            status_key = {
                AdRequest.Status.APPROVED: "ad_status_approved",
                AdRequest.Status.PENDING_AI: "ad_status_pending",
                AdRequest.Status.PENDING_MANUAL: "ad_status_pending",
                AdRequest.Status.NEEDS_REVISION: "ad_status_needs_revision",
                AdRequest.Status.REJECTED: "ad_status_rejected",
                AdRequest.Status.SOLVED: "ad_status_approved",
                AdRequest.Status.EXPIRED: "ad_status_rejected",
            }
            reason_label = get_message("rejection_reason_label", lang)
            lines = [intro]
            keyboard = []
            manage_label = get_message("my_ads_btn_manage", lang)
            for ad in ads:
                try:
                    st = status_key.get(ad.status, "ad_status_pending")
                    status_text = get_message(st, lang)
                    content = ad.content or ""
                    preview = content[:60].replace("\n", " ") + ("…" if len(content) > 60 else "")
                    lines.append(get_message("my_ads_item", lang).format(preview=preview, status=status_text))
                    if ad.status == AdRequest.Status.REJECTED:
                        reason = (getattr(ad, "rejection_reason", None) or "")[:200]
                        if reason:
                            lines.append(f"  {reason_label}{reason}\n")
                    btn_title = f"{manage_label} • {(preview[:35] + '…') if len(preview) > 35 else preview}"
                    keyboard.append([{"text": btn_title, "callback_data": f"manage_ad:{ad.pk}"}])
                except Exception as e:
                    logger.debug("_reply_my_ads ad format skip: %s", e)
            text = "".join(lines).strip()
            if len(text) > 4090:
                text = text[:4087] + "…"
            back_label = get_message("btn_back_to_home", lang)
            keyboard.append([{"text": back_label, "callback_data": "back_to_home"}])
        reply_markup = {"inline_keyboard": keyboard}
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_ad_detail(
        self,
        session: TelegramSession,
        ad_id: int,
        edit_previous: bool = False,
        message_id: int | None = None,
    ) -> dict:
        """Show one ad's details (category, text, phone) with Edit / Delete / Back to List. Owner-only."""
        ad = self._get_user_ad(session, ad_id)
        lang = session.language or "en"
        if not ad:
            text = get_message("ad_not_found", lang)
            reply_markup = {"inline_keyboard": [[{"text": get_message("btn_back_to_list", lang), "callback_data": "list_ads"}]]}
        else:
            cat_name = ad.get_category_display() if ad.category else get_message("category_other", lang)
            content = (ad.content or "")[:1500] + ("…" if len(ad.content or "") > 1500 else "")
            contact = ad.contact_snapshot or {}
            phone = (contact.get("phone") or "") if isinstance(contact, dict) else "—"
            status_key = {
                AdRequest.Status.APPROVED: "ad_status_approved",
                AdRequest.Status.PENDING_AI: "ad_status_pending",
                AdRequest.Status.PENDING_MANUAL: "ad_status_pending",
                AdRequest.Status.NEEDS_REVISION: "ad_status_needs_revision",
                AdRequest.Status.REJECTED: "ad_status_rejected",
                AdRequest.Status.SOLVED: "ad_status_approved",
                AdRequest.Status.EXPIRED: "ad_status_rejected",
            }
            status_text = get_message(status_key.get(ad.status, "ad_status_pending"), lang)
            text = (
                f"{get_message('ad_detail_category', lang)} {cat_name}\n"
                f"{get_message('ad_detail_status', lang)} {status_text}\n\n"
                f"{get_message('ad_detail_text', lang)}\n{content}\n\n"
                f"{get_message('ad_detail_phone', lang)} {phone}"
            )
            if len(text) > 4090:
                text = text[:4087] + "…"
            edit_btn = get_message("btn_edit_ad", lang)
            delete_btn = get_message("btn_delete_ad", lang)
            back_btn = get_message("btn_back_to_list", lang)
            reply_markup = {
                "inline_keyboard": [
                    [{"text": edit_btn, "callback_data": f"edit_ad:{ad.pk}"}, {"text": delete_btn, "callback_data": f"delete_ad:{ad.pk}"}],
                    [{"text": back_btn, "callback_data": "list_ads"}],
                ]
            }
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_delete_confirm(
        self,
        session: TelegramSession,
        ad_id: int,
        edit_previous: bool = False,
        message_id: int | None = None,
    ) -> dict:
        """Ask 'Are you sure you want to delete this ad?' with Yes / Cancel. Owner-only."""
        ad = self._get_user_ad(session, ad_id)
        lang = session.language or "en"
        if not ad:
            text = get_message("ad_not_found", lang)
            reply_markup = {"inline_keyboard": [[{"text": get_message("btn_back_to_list", lang), "callback_data": "list_ads"}]]}
        else:
            text = get_message("delete_confirm_text", lang)
            yes_btn = get_message("delete_confirm_yes", lang)
            cancel_btn = get_message("delete_confirm_cancel", lang)
            reply_markup = {
                "inline_keyboard": [
                    [{"text": yes_btn, "callback_data": f"confirm_delete:yes:{ad.pk}"}, {"text": cancel_btn, "callback_data": f"confirm_delete:no:{ad.pk}"}],
                ]
            }
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_after_category(self, session: TelegramSession, edit_previous: bool = False, message_id: int | None = None) -> dict:
        """After category selected: long explanation + enter ad text instructions, one message, with Back to Home."""
        lang = session.language or "en"
        category = (session.context or {}).get("category", "other")
        category_name = _get_category_display(category, lang)
        part1 = get_message("category_explanation", lang).format(category_name=category_name)
        part2 = get_message("enter_ad_text_detailed", lang).format(category_name=category_name)
        text = f"{part1}\n\n———\n\n{part2}"
        back_label = get_message("btn_back_to_home", lang)
        reply_markup = {"inline_keyboard": [[{"text": back_label, "callback_data": "back_to_home"}]]}
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_enter_content(
        self,
        session: TelegramSession,
        old_content: str = "",
        edit_previous: bool = False,
        message_id: int | None = None,
    ) -> dict:
        lang = session.language or "en"
        category = (session.context or {}).get("category", "other")
        category_name = _get_category_display(category, lang)
        text = get_message("enter_ad_text_detailed", lang).format(category_name=category_name)
        if old_content:
            text = f"{text}\n\n———\n{old_content[:500]}\n———"
        back_label = get_message("btn_back_to_home", lang)
        reply_markup = {"inline_keyboard": [[{"text": back_label, "callback_data": "back_to_home"}]]}
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_ad_content_validation_error(
        self,
        session: TelegramSession,
        edit_previous: bool = False,
        message_id: int | None = None,
        *,
        state_enter_content: bool = True,
    ) -> dict:
        """
        Send warning that emojis/stickers/GIFs are not allowed. Does not advance state;
        user stays in ENTER_CONTENT or RESUBMIT_EDIT so their next message is treated as a retry.
        """
        lang = session.language or "en"
        text = get_message("ad_content_validation_error", lang)
        back_label = get_message("btn_back_to_home", lang)
        reply_markup = {"inline_keyboard": [[{"text": back_label, "callback_data": "back_to_home"}]]}
        out = {"text": text, "reply_markup": reply_markup}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_select_category(self, session: TelegramSession, edit_previous: bool = False, message_id: int | None = None) -> dict:
        lang = session.language or "en"
        text = get_message("select_category_prompt", lang)
        keyboard = []
        for cat in _get_active_categories():
            display = cat.name
            keyboard.append([{"text": display, "callback_data": cat.slug}])
        back_label = get_message("btn_back_to_home", lang)
        keyboard.append([{"text": back_label, "callback_data": "back_to_home"}])
        out = {"text": text, "reply_markup": {"inline_keyboard": keyboard}}
        if edit_previous and message_id is not None:
            out["edit_previous"] = True
            out["message_id"] = message_id
        return out

    def _reply_confirm(self, session: TelegramSession, edit_previous: bool = False, message_id: int | None = None) -> dict:
        lang = session.language or "en"
        content = (session.context or {}).get("content", "")
        category = (session.context or {}).get("category", "other")
        cat_label = _get_category_display(category, lang)
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
        """Ask for phone via ReplyKeyboardMarkup with single button: Share Phone Number (request_contact=True)."""
        lang = session.language or "en"
        text = get_message("ask_contact", lang)
        reply_markup = {
            "keyboard": [[{"text": get_message("share_contact_btn", lang), "request_contact": True}]],
            "one_time_keyboard": True,
            "resize_keyboard": True,
        }
        return {"text": text, "reply_markup": reply_markup}

    def _reply_ask_contact_use_button(self, session: TelegramSession) -> dict:
        """User sent text instead of contact: ask them to use the share button. Same keyboard so button stays visible."""
        lang = session.language or "en"
        text = get_message("ask_contact_use_button", lang)
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

    def _reply_contact_not_verified(self, session: TelegramSession) -> dict:
        """Contact shared does not belong to this user (user_id mismatch)."""
        lang = session.language or "en"
        text = get_message("contact_not_verified", lang)
        return {"text": text, "reply_markup": self._reply_ask_contact(session).get("reply_markup")}

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
        back_label = get_message("btn_back_to_home", lang)
        reply_markup = {"inline_keyboard": [[{"text": back_label, "callback_data": "back_to_home"}]]}
        return {"text": text, "reply_markup": reply_markup}

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
        back_label = get_message("btn_back_to_home", lang)
        reply_markup = {"inline_keyboard": [[{"text": back_label, "callback_data": "back_to_home"}]]}
        return {"text": text, "reply_markup": reply_markup}

    def _reply_resubmit_error(self, session: TelegramSession, error_key: str) -> dict:
        lang = session.language or "en"
        text = get_message(error_key, lang)
        back_label = get_message("btn_back_to_home", lang)
        create_label = get_message("create_new_ad", lang)
        reply_markup = {
            "inline_keyboard": [
                [{"text": back_label, "callback_data": "back_to_home"}, {"text": create_label, "callback_data": "create_ad"}]
            ]
        }
        return {"text": text, "reply_markup": reply_markup}
