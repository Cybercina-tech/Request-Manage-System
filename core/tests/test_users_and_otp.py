"""
Iraniu — Tests for user creation, update, contact saving, OTP generation, expiry.
OTP sending is not implemented; generation/verify are tested.
"""

from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from unittest.mock import patch

from core.models import TelegramUser, VerificationCode, SiteConfiguration
from core.services.users import (
    get_or_create_user_from_update,
    update_contact_info,
    validate_phone,
    validate_email,
)
from core.services.otp import generate_code, verify_code, hash_code
from core.conf import ENABLE_OTP


class GetOrCreateUserFromUpdateTests(TestCase):
    """User creation and update from webhook update."""

    def setUp(self):
        SiteConfiguration.get_config()

    def test_create_user_from_message(self):
        update = {
            "message": {
                "from": {
                    "id": 12345,
                    "username": "jdoe",
                    "first_name": "John",
                    "last_name": "Doe",
                    "language_code": "en",
                    "is_bot": False,
                },
                "chat": {"id": 12345},
                "text": "hi",
            }
        }
        user = get_or_create_user_from_update(update)
        self.assertIsNotNone(user)
        self.assertEqual(user.telegram_user_id, 12345)
        self.assertEqual(user.username, "jdoe")
        self.assertEqual(user.first_name, "John")
        self.assertEqual(user.last_name, "Doe")
        self.assertEqual(user.language_code, "en")
        self.assertFalse(user.is_bot)
        self.assertIsNotNone(user.last_seen)

    def test_update_user_on_second_call(self):
        update = {
            "message": {
                "from": {
                    "id": 999,
                    "username": "updated",
                    "first_name": "Jane",
                    "last_name": "",
                    "language_code": "fa",
                    "is_bot": False,
                },
                "chat": {"id": 999},
            }
        }
        u1 = get_or_create_user_from_update(update)
        self.assertEqual(u1.username, "updated")
        self.assertEqual(u1.language_code, "fa")
        u1.refresh_from_db()
        update["message"]["from"]["username"] = "newhandle"
        u2 = get_or_create_user_from_update(update)
        self.assertEqual(u1.pk, u2.pk)
        u2.refresh_from_db()
        self.assertEqual(u2.username, "newhandle")

    def test_returns_none_when_no_from(self):
        self.assertIsNone(get_or_create_user_from_update({}))
        self.assertIsNone(get_or_create_user_from_update({"message": {"chat": {"id": 1}, "text": "hi"}}))


class ContactValidationTests(TestCase):
    """Phone E.164 and email validation."""

    def test_validate_phone_e164(self):
        self.assertEqual(validate_phone("+989123456789"), "+989123456789")
        self.assertEqual(validate_phone("989123456789"), "+989123456789")
        self.assertEqual(validate_phone("+1 234 567 8901"), "+12345678901")

    def test_validate_phone_rejects_invalid(self):
        with self.assertRaises(ValueError):
            validate_phone("not a number")
        with self.assertRaises(ValueError):
            validate_phone("123")  # too short
        with self.assertRaises(ValueError):
            validate_phone("")

    def test_validate_email(self):
        self.assertEqual(validate_email("user@example.com"), "user@example.com")
        self.assertEqual(validate_email("  User@Example.COM  "), "user@example.com")

    def test_validate_email_rejects_invalid(self):
        with self.assertRaises(ValueError):
            validate_email("invalid")
        with self.assertRaises(ValueError):
            validate_email("")


class UpdateContactInfoTests(TestCase):
    """Saving phone/email to TelegramUser."""

    def setUp(self):
        SiteConfiguration.get_config()
        self.user = TelegramUser.objects.create(telegram_user_id=111, username="u1")

    def test_save_phone_marks_unverified(self):
        update_contact_info(self.user, phone="+989123456789")
        self.user.refresh_from_db()
        self.assertEqual(self.user.phone_number, "+989123456789")
        self.assertFalse(self.user.phone_verified)

    def test_save_email_marks_unverified(self):
        update_contact_info(self.user, email="u@example.com")
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "u@example.com")
        self.assertFalse(self.user.email_verified)

    def test_mark_verified(self):
        update_contact_info(self.user, phone="+989123456789", mark_phone_verified=True)
        self.user.refresh_from_db()
        self.assertTrue(self.user.phone_verified)


class OTPTests(TestCase):
    """OTP generation (hashed), verify, expiry. ENABLE_OTP is False by default."""

    def setUp(self):
        SiteConfiguration.get_config()
        self.user = TelegramUser.objects.create(telegram_user_id=222, username="u2")

    def test_hash_code(self):
        h = hash_code("123456")
        self.assertIsInstance(h, str)
        self.assertNotEqual(h, "123456")
        self.assertEqual(h, hash_code("123456"))

    def test_generate_code_when_otp_disabled_returns_none(self):
        self.assertFalse(ENABLE_OTP)
        code = generate_code(self.user, VerificationCode.Channel.EMAIL)
        self.assertIsNone(code)
        self.assertEqual(VerificationCode.objects.count(), 0)

    def test_verify_code_when_otp_disabled_returns_false(self):
        self.assertFalse(verify_code(self.user, VerificationCode.Channel.EMAIL, "123456"))

    @patch("core.services.otp.ENABLE_OTP", True)
    def test_generate_and_verify_when_otp_enabled(self):
        code = generate_code(self.user, VerificationCode.Channel.EMAIL, expiry_minutes=10)
        self.assertIsNotNone(code)
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertEqual(VerificationCode.objects.count(), 1)
        vc = VerificationCode.objects.get()
        self.assertNotEqual(vc.code_hashed, code)
        self.assertTrue(verify_code(self.user, VerificationCode.Channel.EMAIL, code))
        vc.refresh_from_db()
        self.assertTrue(vc.used)
        self.assertFalse(verify_code(self.user, VerificationCode.Channel.EMAIL, code))  # already used

    def test_expiry_handling(self):
        # Expired code should not verify
        past = timezone.now() - timedelta(minutes=5)
        VerificationCode.objects.create(
            user=self.user,
            channel=VerificationCode.Channel.EMAIL,
            code_hashed=hash_code("123456"),
            expires_at=past,
            used=False,
        )
        with patch("core.services.otp.ENABLE_OTP", True):
            self.assertFalse(verify_code(self.user, VerificationCode.Channel.EMAIL, "123456"))
