"""
Iraniu — Tests for image engine and ad banner generation.
"""

from django.test import TestCase

from core.models import default_adtemplate_coordinates
from core.services.image_engine import generate_example_ad_banner


class PhoneLayerDefaultsTests(TestCase):
    """Verify phone layer default coordinates match spec (size 48)."""

    def test_default_phone_coords_size_48(self):
        coords = default_adtemplate_coordinates()
        phone = coords.get("phone", {})
        self.assertEqual(phone.get("size"), 48, "Phone size should default to 48")

    def test_default_phone_coords_spec(self):
        coords = default_adtemplate_coordinates()
        phone = coords.get("phone", {})
        self.assertEqual(phone.get("x"), 300)
        self.assertEqual(phone.get("y"), 1150)
        self.assertEqual(phone.get("max_width"), 450)
        self.assertEqual(phone.get("color"), "#131111")
        self.assertEqual(phone.get("letter_spacing"), 2)


class GenerateExampleBannerTests(TestCase):
    """Test example banner generation uses default phone coords."""

    def setUp(self):
        from core.models import AdTemplate
        self.template = AdTemplate.objects.create(
            name="Test Template",
            coordinates={"phone": {"size": 999}},
        )
        self.template.save()

    def test_generate_example_uses_default_phone_coords(self):
        path = generate_example_ad_banner(output_filename="test_phone_defaults.png")
        self.assertIsNotNone(path)
        self.assertIn("test_phone_defaults.png", path)
