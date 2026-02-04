"""
Iranio — Tests for conversation engine (state machine, i18n, submit flow).
"""

from django.test import TestCase, override_settings
from django.utils import timezone

from core.models import TelegramBot, TelegramSession, AdRequest, SiteConfiguration, TelegramUser
from core.i18n import get_message, MESSAGES
from core.services.conversation import ConversationEngine, CATEGORY_KEYS
from core.services.submit_ad_service import SubmitAdService


class GetMessageTests(TestCase):
    """Never hardcode text; always use get_message."""

    def test_get_message_en(self):
        self.assertEqual(get_message("start", "en"), "Welcome to Iranio. Please choose your language.")
        self.assertEqual(get_message("create_new_ad", "en"), "Create new ad")

    def test_get_message_fa(self):
        self.assertEqual(get_message("start", "fa"), "به ایرانيو خوش آمدید. لطفاً زبان خود را انتخاب کنید.")
        self.assertEqual(get_message("create_new_ad", "fa"), "ثبت آگهی جدید")

    def test_get_message_fallback(self):
        self.assertEqual(get_message("start", None), "Welcome to Iranio. Please choose your language.")
        self.assertEqual(get_message("unknown_key", "en"), "unknown_key")


class ConversationEngineTests(TestCase):
    """State machine: START -> SELECT_LANGUAGE -> MAIN_MENU -> ENTER_CONTENT -> SELECT_CATEGORY -> CONFIRM -> SUBMITTED."""

    def setUp(self):
        SiteConfiguration.get_config()  # ensure singleton
        self.bot = TelegramBot.objects.create(
            name="TestBot",
            username="testbot",
            is_active=True,
            status=TelegramBot.Status.ONLINE,
        )
        self.bot.set_token("123:ABC")  # fake token
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

    def test_language_choice_goes_to_ask_contact(self):
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.SELECT_LANGUAGE
        session.save(update_fields=["state"])
        out = self.engine.process_update(session, callback_data="en")
        session.refresh_from_db()
        self.assertEqual(session.language, "en")
        self.assertEqual(session.state, TelegramSession.State.ASK_CONTACT)

    def test_contact_skip_goes_to_main_menu(self):
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.ASK_CONTACT
        session.language = "en"
        session.save(update_fields=["state", "language"])
        out = self.engine.process_update(session, callback_data="contact_skip")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.MAIN_MENU)

    def test_main_menu_create_ad_goes_to_enter_content(self):
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.MAIN_MENU
        session.language = "en"
        session.save(update_fields=["state", "language"])
        out = self.engine.process_update(session, callback_data="create_ad")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.ENTER_CONTENT)

    def test_enter_content_goes_to_select_category(self):
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.ENTER_CONTENT
        session.language = "en"
        session.save(update_fields=["state", "language"])
        out = self.engine.process_update(session, text="My ad text here")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SELECT_CATEGORY)
        self.assertEqual(session.context.get("content"), "My ad text here")

    def test_select_category_goes_to_confirm(self):
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.SELECT_CATEGORY
        session.language = "en"
        session.context = {"content": "Ad text"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(session, callback_data="rent")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.CONFIRM)
        self.assertEqual(session.context.get("category"), "rent")

    def test_confirm_yes_creates_ad_and_goes_to_submitted(self):
        user = TelegramUser.objects.create(telegram_user_id=12345, username="u")
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.CONFIRM
        session.language = "en"
        session.context = {"content": "Ad content", "category": "other"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(session, callback_data="confirm_yes")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.SUBMITTED)
        self.assertEqual(AdRequest.objects.filter(telegram_user_id=12345, bot=self.bot).count(), 1)
        ad = AdRequest.objects.get(telegram_user_id=12345, bot=self.bot)
        self.assertEqual(ad.content, "Ad content")
        self.assertEqual(ad.category, "other")
        self.assertEqual(ad.user_id, user.pk)
        self.assertIn("phone", ad.contact_snapshot)
        self.assertIn("verified_phone", ad.contact_snapshot)

    def test_confirm_no_returns_to_main_menu(self):
        TelegramUser.objects.get_or_create(telegram_user_id=12345, defaults={"username": "u"})
        session = self.engine.get_or_create_session(telegram_user_id=12345)
        session.state = TelegramSession.State.CONFIRM
        session.language = "en"
        session.context = {"content": "Ad content", "category": "other"}
        session.save(update_fields=["state", "language", "context"])
        out = self.engine.process_update(session, callback_data="confirm_no")
        session.refresh_from_db()
        self.assertEqual(session.state, TelegramSession.State.MAIN_MENU)
        self.assertEqual(AdRequest.objects.filter(telegram_user_id=12345).count(), 0)


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
            content="Test ad",
            category="other",
            telegram_user_id=999,
            bot=self.bot,
            user=self.user,
            contact_snapshot={"phone": "+989123456789", "email": "", "verified_phone": False, "verified_email": False},
        )
        self.assertIsNotNone(ad)
        self.assertEqual(ad.content, "Test ad")
        self.assertEqual(ad.category, "other")
        self.assertEqual(ad.telegram_user_id, 999)
        self.assertEqual(ad.bot_id, self.bot.pk)
        self.assertEqual(ad.user_id, self.user.pk)
        self.assertEqual(ad.contact_snapshot.get("phone"), "+989123456789")
        self.assertEqual(ad.status, AdRequest.Status.PENDING_MANUAL)
