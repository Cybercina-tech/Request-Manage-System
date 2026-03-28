"""Tests for E.164 export helpers used by webhook and Partner API."""

from django.test import TestCase

from core.models import AdRequest, Category, TelegramUser
from core.utils.phone_export import (
    format_ad_phone_e164_export,
    format_e164_plus_from_raw,
    resolve_ad_request_phone,
)


class PhoneExportTests(TestCase):
    def test_format_e164_plus_from_raw(self):
        self.assertEqual(format_e164_plus_from_raw(""), "")
        self.assertEqual(format_e164_plus_from_raw("   "), "")
        self.assertEqual(format_e164_plus_from_raw("+98 912 345 6789"), "+989123456789")
        self.assertEqual(format_e164_plus_from_raw("09123456789"), "+09123456789")

    def test_format_e164_truncates_to_15_digits(self):
        long_digits = "1" * 20
        out = format_e164_plus_from_raw(long_digits)
        self.assertEqual(out, "+" + "1" * 15)

    def test_resolve_ad_request_phone_priority(self):
        cat = Category.objects.create(name="TestCat", slug="test-phone-export-cat")
        tu = TelegramUser.objects.create(telegram_user_id=999001, phone_number="+989000000000")
        ad = AdRequest.objects.create(
            content="test",
            category=cat,
            status=AdRequest.Status.PENDING_MANUAL,
            phone_number="+989111111111",
        )
        self.assertEqual(resolve_ad_request_phone(ad), "+989111111111")

        ad.phone_number = ""
        ad.contact_snapshot = {"phone": "+989222222222"}
        ad.save()
        self.assertEqual(resolve_ad_request_phone(ad), "+989222222222")

        ad.contact_snapshot = {}
        ad.user = tu
        ad.save()
        self.assertEqual(resolve_ad_request_phone(ad), "+989000000000")

    def test_format_ad_phone_e164_export(self):
        cat = Category.objects.create(name="TestCat2", slug="test-phone-export-cat2")
        ad = AdRequest.objects.create(
            content="x",
            category=cat,
            status=AdRequest.Status.APPROVED,
            phone_number="+98 912 345 6789",
        )
        self.assertEqual(format_ad_phone_e164_export(ad), "+989123456789")
