"""
Iraniu — Tests for conversation engine (state machine, i18n, submit flow).
Covers: language selection with inline buttons, contact at end, data collection order,
inline edit/back/confirm, emoji messages (EN/FA), resubmit deep link.
"""

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import TelegramBot, TelegramSession, AdRequest, Category, SiteConfiguration, TelegramUser
from core.i18n import get_message
from core.services.conversation import ConversationEngine
from core.services.submit_ad_service import SubmitAdService
from core.services.telegram_update_handler import should_skip_duplicate_update, LAST_PROCESSED_UPDATE_ID_KEY


class DuplicateUpdateDedupTests(TestCase):
    """Dedup: same update_id processed twice must not produce two responses."""

    def test_should_skip_duplicate_update_no_last(self):
        """No last_processed_update_id in context -> do not skip."""
        bot = TelegramBot.objects.create(name="T", username="t", is_active=True)
        bot.set_token("x:y")
        bot.save()
        session = TelegramSession.objects.create(telegram_user_id=999, bot=bot, state=TelegramSession.State.MAIN_MENU, context={})
        self.assertFalse(should_skip_duplicate_update(session, 100))
        self.assertFalse(should_skip_duplicate_update(session, None))

    def test_should_skip_duplicate_update_older_or_equal_skipped(self):
        """update_id <= last_processed_update_id -> skip (duplicate or old)."""
        bot = TelegramBot.objects.create(name="T", username="t", is_active=True)
        bot.set_token("x:y")
        bot.save()
        session = TelegramSession.objects.create(
            telegram_user_id=999,
            bot=bot,
            state=TelegramSession.State.MAIN_MENU,
            context={LAST_PROCESSED_UPDATE_ID_KEY: 100},
        )
        self.assertTrue(should_skip_duplicate_update(session, 99))
        self.assertTrue(should_skip_duplicate_update(session, 100))

    def test_should_skip_duplicate_update_newer_not_skipped(self):
        """update_id > last_processed_update_id -> do not skip."""
        bot = TelegramBot.objects.create(name="T", username="t", is_active=True)
        bot.set_token("x:y")
        bot.save()
        session = TelegramSession.objects.create(
            telegram_user_id=999,
            bot=bot,
            state=TelegramSession.State.MAIN_MENU,
            context={LAST_PROCESSED_UPDATE_ID_KEY: 100},
        )
        self.assertFalse(should_skip_duplicate_update(session, 101))


class GetMessageTests(TestCase):
    """Never hardcode text; always use get_message. Emojis in messages."""

    def test_get_message_en(self):
        self.assertIn("Welcome", get_message("start", "en"))
        self.assertIn("👋", get_message("start", "en"))
        self.assertIn("Create new ad", get_message("create_new_ad", "en"))
        self.assertIn("✨", get_message("create_new_ad", "en"))

    def test_get_message_fa(self):
        self.assertIn("خوش آمدید", get_message("start", "fa"))
        self.assertIn("👋", get_message("start", "fa"))
        self.assertIn("ثبت آگهی جدید", get_message("create_new_ad", "fa"))

    def test_get_message_fallback(self):
        self.assertIn("Welcome", get_message("start", None))
        self.assertEqual(get_message("unknown_key", "en"), "unknown_key")

    def test_emoji_messages_en(self):
        self.assertIn("✅", get_message("confirm_yes_btn", "en"))
        self.assertIn("❌", get_message("cancel", "en"))
        self.assertIn("🎉", get_message("submitted", "en"))

    def test_emoji_messages_fa(self):
        self.assertIn("✅", get_message("confirm_yes_btn", "fa"))
        self.assertIn("🎉", get_message("submitted", "fa"))


class ConversationEngineTests(TestCase):
    """State machine: START -> SELECT_LANGUAGE -> MAIN_MENU -> SELECT_CATEGORY -> ENTER_CONTENT -> CONFIRM -> ASK_CONTACT -> ENTER_EMAIL -> SUBMITTED (category first, then ad text)."""

    def setUp(self):
        SiteConfiguration.get_config()
        self.bot = TelegramBot.objects.create(
            name="TestBot",
            username="testbot",
            is_active=True,
            status=TelegramBot.Status.ONLINE,
        )
        self.bot.set_token("123:ABC")
        self.bot.save()
        self.engine = ConversationEngine(self.bot)

    def test_start_sends_language_selection(self):
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        self.assertEqual(session.state, TelegramSession.State.START)
        out = self.engine.process_update(session, text="/start")
        self.assertIn("text", out)
        self.assertIn("reply_markup", out)
        self.assertIn("inline_keyboard", out["reply_markup"])
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SELECT_LANGUAGE)

    def test_language_choice_goes_to_main_menu(self):
        """Language selection with inline button leads to main menu (no contact at start)."""
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.SELECT_LANGUAGE
        session.save(update_fields=["state"])
        out = self.engine.process_update(session, callback_data="en")
        session.refresh_from_db()
        self.assertEqual(session.language, "en")
        self.assertEqual(session.state, TelegramSession.State.MAIN_MENU)
        self.assertIn("inline_keyboard", out["reply_markup"])

    def test_language_choice_fa_goes_to_main_menu(self):
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.SELECT_LANGUAGE
        session.save(update_fields=["state"])
        out = self.engine.process_update(session, callback_data="fa")
        session.refresh_from_db()
        self.assertEqual(session.language, "fa")
        self.assertEqual(session.state, TelegramSession.State.MAIN_MENU)

    def test_confirm_yes_goes_to_ask_email(self):
        """Data collection at end: after confirm we ask email (inline skip)."""
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.CONFIRM
        session.language = "en"
        session.context = {"content": "اجاره آپارتمان در تهران", "category": "other"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(session, callback_data="confirm_yes")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.ENTER_EMAIL)
        self.assertIn("inline_keyboard", out["reply_markup"])

    def test_contact_shared_goes_to_select_category(self):
        """Share contact when phone not verified -> SELECT_CATEGORY (then can create ad)."""
        user = TelegramUser.objects.create(telegram_user_id=12345, username="u")
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.ASK_CONTACT
        session.language = "en"
        session.context = {"content": "فروش خودرو پژو", "category": "other"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(
            session, contact_phone="+989123456789", contact_user_id=12345
        )
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SELECT_CATEGORY)
        user.refresh_from_db()
        self.assertEqual(user.phone_number, "+989123456789")
        self.assertTrue(user.phone_verified)

    def test_email_skip_submits_ad(self):
        """After ENTER_EMAIL (ask email at end), email_skip creates ad and goes to SUBMITTED."""
        user = TelegramUser.objects.create(telegram_user_id=12345, username="u")
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.ENTER_EMAIL
        session.language = "en"
        session.context = {"content": "اجاره آپارتمان در تهران", "category": "other"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(session, callback_data="email_skip")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SUBMITTED)
        self.assertEqual(AdRequest.objects.filter(telegram_user_id=12345, bot=self.bot).count(), 1)
        ad = AdRequest.objects.get(telegram_user_id=12345, bot=self.bot)
        self.assertEqual(ad.content, "اجاره آپارتمان در تهران")
        self.assertEqual(ad.user_id, user.pk)

    def test_main_menu_create_ad_goes_to_select_category(self):
        """Category-first flow: Create ad -> SELECT_CATEGORY (not ENTER_CONTENT)."""
        user, _ = TelegramUser.objects.get_or_create(
            telegram_user_id=12345,
            defaults={"username": "u"},
        )
        user.phone_verified = True
        user.save(update_fields=["phone_verified"])
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.MAIN_MENU
        session.language = "en"
        session.save(update_fields=["state", "language"])
        out = self.engine.process_update(session, callback_data="create_ad")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SELECT_CATEGORY)
        self.assertIn("inline_keyboard", out["reply_markup"])

    def test_select_category_goes_to_enter_content(self):
        """Category chosen -> ENTER_CONTENT, context has category."""
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.SELECT_CATEGORY
        session.language = "en"
        session.save(update_fields=["state", "language"])
        out = self.engine.process_update(session, callback_data="rent")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.ENTER_CONTENT)
        self.assertEqual(session.context.get("category"), "rent")

    def test_enter_content_goes_to_confirm(self):
        """After category, user sends text -> CONFIRM, context has content."""
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.ENTER_CONTENT
        session.language = "en"
        session.context = {"category": "other"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(session, text="اجاره آپارتمان در تهران")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.CONFIRM)
        self.assertEqual(session.context.get("content"), "اجاره آپارتمان در تهران")
        self.assertEqual(session.context.get("category"), "other")

    def test_invalid_category_stays_in_select_category(self):
        """Invalid category input -> re-prompt SELECT_CATEGORY."""
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.SELECT_CATEGORY
        session.language = "en"
        session.save(update_fields=["state", "language"])
        out = self.engine.process_update(session, callback_data="invalid_cat")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SELECT_CATEGORY)
        self.assertIn("inline_keyboard", out["reply_markup"])

    def test_empty_content_reprompts_enter_content(self):
        """Empty text in ENTER_CONTENT -> re-prompt, stay in ENTER_CONTENT."""
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.ENTER_CONTENT
        session.language = "en"
        session.context = {"category": "rent"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(session, text="   ")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.ENTER_CONTENT)
        self.assertIn("text", out)

    def test_full_flow_category_then_content_then_confirm_submit(self):
        """Full flow: MAIN_MENU -> SELECT_CATEGORY -> ENTER_CONTENT -> CONFIRM -> ... -> SUBMITTED."""
        user = TelegramUser.objects.create(
            telegram_user_id=12345, username="u", phone_verified=True
        )
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.MAIN_MENU
        session.language = "en"
        session.save(update_fields=["state", "language"])
        self.engine.process_update(session, callback_data="create_ad")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SELECT_CATEGORY)
        self.engine.process_update(session, callback_data="rent")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.ENTER_CONTENT)
        self.assertEqual(session.context.get("category"), "rent")
        self.engine.process_update(session, text="اجاره آپارتمان در تهران")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.CONFIRM)
        self.assertEqual(session.context.get("content"), "اجاره آپارتمان در تهران")
        self.engine.process_update(session, callback_data="confirm_yes")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.ENTER_EMAIL)
        self.engine.process_update(session, callback_data="email_skip")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SUBMITTED)
        self.assertEqual(AdRequest.objects.filter(telegram_user_id=12345, bot=self.bot).count(), 1)
        ad = AdRequest.objects.get(telegram_user_id=12345, bot=self.bot)
        self.assertEqual(ad.content, "اجاره آپارتمان در تهران")
        self.assertEqual(ad.category.slug if ad.category else None, "rent")
        self.assertEqual(ad.user_id, user.pk)

    def test_confirm_yes_then_skip_email_creates_ad(self):
        """Confirm -> ask email -> skip -> ad created."""
        user = TelegramUser.objects.create(telegram_user_id=12345, username="u")
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.CONFIRM
        session.language = "en"
        session.context = {"content": "اجاره آپارتمان در تهران", "category": "other"}
        session.save(update_fields=["state", "language", "context"])
        self.engine.process_update(session, callback_data="confirm_yes")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.ENTER_EMAIL)
        self.engine.process_update(session, callback_data="email_skip")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SUBMITTED)
        self.assertEqual(AdRequest.objects.filter(telegram_user_id=12345, bot=self.bot).count(), 1)
        ad = AdRequest.objects.get(telegram_user_id=12345, bot=self.bot)
        self.assertEqual(ad.content, "اجاره آپارتمان در تهران")
        self.assertEqual(ad.user_id, user.pk)

    def test_confirm_no_returns_to_main_menu(self):
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.CONFIRM
        session.language = "en"
        session.context = {"content": "اجاره آپارتمان در تهران", "category": "other"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(session, callback_data="confirm_no")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.MAIN_MENU)
        self.assertEqual(AdRequest.objects.filter(telegram_user_id=12345).count(), 0)

    def test_confirm_back_returns_to_select_category(self):
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.CONFIRM
        session.language = "en"
        session.context = {"content": "فروش خودرو پژو", "category": "rent"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(session, callback_data="confirm_back", message_id=99)
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SELECT_CATEGORY)
        self.assertTrue(out.get("edit_previous"))
        self.assertEqual(out.get("message_id"), 99)

    def test_confirm_edit_returns_to_enter_content(self):
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.CONFIRM
        session.language = "en"
        session.context = {"content": "آگهی قبلی", "category": "rent"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(session, callback_data="confirm_edit")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.ENTER_CONTENT)
        self.assertIn("آگهی قبلی", out["text"])

    def test_inline_button_returns_edit_previous_and_message_id(self):
        """When processing callback (e.g. category chosen), response includes edit_previous and message_id."""
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.SELECT_CATEGORY
        session.language = "en"
        session.save(update_fields=["state", "language"])
        out = self.engine.process_update(session, callback_data="rent", message_id=42)
        self.assertTrue(out.get("edit_previous"))
        self.assertEqual(out.get("message_id"), 42)
        # After category we go to ENTER_CONTENT (prompt for ad text)
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.ENTER_CONTENT)
        self.assertIn("text", out)

    def test_start_resubmit_invalid_uuid_shows_error(self):
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.language = "en"
        session.save(update_fields=["language"])
        out = self.engine.process_update(session, text="/start resubmit_not-a-uuid")
        self.assertIn("text", out)
        self.assertIn("inline_keyboard", out.get("reply_markup", {}))
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.MAIN_MENU)

    def test_start_resubmit_valid_rejected_ad_enters_resubmit_edit(self):
        user = TelegramUser.objects.create(telegram_user_id=12345, username="u")
        ad = AdRequest.objects.create(
            content="آگهی رد شده قبلی",
            category=Category.objects.get(slug='other'),
            status=AdRequest.Status.REJECTED,
            telegram_user_id=12345,
            bot=self.bot,
            user=user,
        )
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.language = "en"
        session.save(update_fields=["language"])
        out = self.engine.process_update(session, text=f"/start resubmit_{ad.uuid}")
        self.assertIn("text", out)
        self.assertIn("آگهی رد شده قبلی", out["text"])
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.RESUBMIT_EDIT)
        self.assertEqual(session.context.get("mode"), "resubmit")
        self.assertEqual(session.context.get("original_ad_id"), str(ad.uuid))

    def test_resubmit_ad_not_rejected_shows_error(self):
        user = TelegramUser.objects.create(telegram_user_id=12345, username="u")
        ad = AdRequest.objects.create(
            content="آگهی تأیید شده",
            category=Category.objects.get(slug='other'),
            status=AdRequest.Status.APPROVED,
            telegram_user_id=12345,
            bot=self.bot,
            user=user,
        )
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.language = "en"
        session.save(update_fields=["language"])
        out = self.engine.process_update(session, text=f"/start resubmit_{ad.uuid}")
        self.assertIn("text", out)
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.MAIN_MENU)

    def test_resubmit_user_mismatch_shows_error(self):
        other_user_id = 99999
        TelegramUser.objects.create(telegram_user_id=other_user_id, username="other")
        ad = AdRequest.objects.create(
            content="آگهی کاربر دیگر",
            category=Category.objects.get(slug='other'),
            status=AdRequest.Status.REJECTED,
            telegram_user_id=other_user_id,
            bot=self.bot,
        )
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.language = "en"
        session.save(update_fields=["language"])
        out = self.engine.process_update(session, text=f"/start resubmit_{ad.uuid}")
        self.assertIn("text", out)
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.MAIN_MENU)

    def test_resubmit_full_flow_creates_new_ad_and_marks_original_solved(self):
        user = TelegramUser.objects.create(telegram_user_id=12345, username="u")
        original = AdRequest.objects.create(
            content="محتوا رد شده",
            category=Category.objects.get(slug='rent'),
            status=AdRequest.Status.REJECTED,
            telegram_user_id=12345,
            bot=self.bot,
            user=user,
        )
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.language = "en"
        session.save(update_fields=["language"])
        self.engine.process_update(session, text=f"/start resubmit_{original.uuid}")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.RESUBMIT_EDIT)
        self.engine.process_update(session, text="متن اصلاح شده جدید")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.RESUBMIT_CONFIRM)
        self.engine.process_update(session, callback_data="confirm_yes")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SUBMITTED)
        original.refresh_from_db()
        self.assertEqual(original.status, AdRequest.Status.SOLVED)
        new_ads = AdRequest.objects.filter(telegram_user_id=12345, bot=self.bot).exclude(uuid=original.uuid)
        self.assertEqual(new_ads.count(), 1)
        self.assertEqual(new_ads.get().content, "متن اصلاح شده جدید")
        self.assertEqual(new_ads.get().category.slug if new_ads.get().category else None, "rent")


class SubmitAdServiceTests(TestCase):
    """Internal submit_ad creates AdRequest and runs AI if enabled."""

    def setUp(self):
        self.config = SiteConfiguration.get_config()
        self.config.is_ai_enabled = False
        self.config.save()
        self.bot = TelegramBot.objects.create(
            name="TestBot",
            username="testbot",
            is_active=True,
        )
        self.bot.set_token("123:ABC")
        self.bot.save()
        self.user = TelegramUser.objects.create(telegram_user_id=999, username="testuser")

    def test_submit_creates_ad(self):
        ad = SubmitAdService.submit(
            content="آگهی تست",
            category=Category.objects.get(slug='other'),
            telegram_user_id=999,
            bot=self.bot,
            user=self.user,
            contact_snapshot={"phone": "+989123456789", "email": "", "verified_phone": False, "verified_email": False},
        )
        self.assertIsNotNone(ad)
        self.assertEqual(ad.content, "آگهی تست")
        self.assertEqual(ad.category.slug if ad.category else None, "other")
        self.assertEqual(ad.telegram_user_id, 999)
        self.assertEqual(ad.bot_id, self.bot.pk)
        self.assertEqual(ad.user_id, self.user.pk)
        self.assertEqual(ad.contact_snapshot.get("phone"), "+989123456789")
        self.assertEqual(ad.status, AdRequest.Status.PENDING_MANUAL)
