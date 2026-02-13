"""
Iraniu — Tests for ad content validators (length 80, Persian-only).
"""

from django.core.exceptions import ValidationError
from django.test import TestCase

from core.validators import (
    AD_CONTENT_MAX_LENGTH,
    validate_ad_content,
    validate_ad_content_length,
    validate_ad_content_persian,
    validate_ad_content_with_feedback,
)


class AdContentLengthValidatorTests(TestCase):
    """Test 80-character limit."""

    def test_valid_length_passes(self):
        validate_ad_content_length("متن کوتاه")
        validate_ad_content_length("a" * 0)  # empty
        validate_ad_content_length("ف" * 80)

    def test_over_80_raises(self):
        with self.assertRaises(ValidationError) as cm:
            validate_ad_content_length("ف" * 81)
        code = cm.exception.error_list[0].code if cm.exception.error_list else None
        self.assertEqual(code, "ad_content_too_long")
        self.assertIn("۸۰", str(cm.exception))


class AdContentPersianValidatorTests(TestCase):
    """Test Persian-only (no Latin letters)."""

    def test_persian_passes(self):
        validate_ad_content_persian("اجاره آپارتمان")
        validate_ad_content_persian("۱۲۳")  # Persian digits
        validate_ad_content_persian("متن با اعداد 123")

    def test_latin_raises(self):
        with self.assertRaises(ValidationError) as cm:
            validate_ad_content_persian("Test ad")
        code = cm.exception.error_list[0].code if cm.exception.error_list else None
        self.assertEqual(code, "ad_content_not_persian")
        self.assertIn("فارسی", str(cm.exception))

    def test_mixed_latin_raises(self):
        with self.assertRaises(ValidationError):
            validate_ad_content_persian("آپارتمان در تهران apartment")


class AdContentFullValidatorTests(TestCase):
    """Test combined validation."""

    def test_valid_content_passes(self):
        validate_ad_content("اجاره آپارتمان در تهران")

    def test_too_long_raises(self):
        with self.assertRaises(ValidationError):
            validate_ad_content("ف" * 81)

    def test_latin_raises(self):
        with self.assertRaises(ValidationError):
            validate_ad_content("Hello world")


class ValidateAdContentWithFeedbackTests(TestCase):
    """Test (is_valid, error_key) return for bot flow."""

    def test_valid_returns_true_none(self):
        valid, err = validate_ad_content_with_feedback("اجاره آپارتمان")
        self.assertTrue(valid)
        self.assertIsNone(err)

    def test_too_long_returns_false_key(self):
        valid, err = validate_ad_content_with_feedback("ف" * 81)
        self.assertFalse(valid)
        self.assertEqual(err, "ad_content_too_long")

    def test_latin_returns_false_key(self):
        valid, err = validate_ad_content_with_feedback("Test")
        self.assertFalse(valid)
        self.assertEqual(err, "ad_content_not_persian")
