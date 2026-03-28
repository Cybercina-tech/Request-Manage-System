"""
Iraniu — Tests for the staff Create Ad web page and submit endpoint.
"""

import json

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from core.models import AdRequest, Category, SiteConfiguration

User = get_user_model()


class CreateAdWebTests(TestCase):
    """Staff-only ad creation via the web panel."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            username='staffuser', password='testpass123', is_staff=True,
        )
        self.client.force_login(self.staff)
        config = SiteConfiguration.get_config()
        config.is_ai_enabled = False
        config.save()
        self.category = Category.objects.filter(slug='other').first()
        if not self.category:
            self.category = Category.objects.create(name='Other', slug='other', is_active=True)

    def _submit(self, **overrides):
        payload = {
            'content': 'آگهی تست از پنل وب',
            'phone': '+989123456789',
            'category': self.category.slug,
        }
        payload.update(overrides)
        return self.client.post(
            reverse('ad_create_submit'),
            data=json.dumps(payload),
            content_type='application/json',
        )

    def test_valid_submission_creates_ad(self):
        response = self._submit()
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data['status'], 'created')
        ad = AdRequest.objects.get(uuid=data['uuid'])
        self.assertEqual(ad.content, 'آگهی تست از پنل وب')
        self.assertEqual(ad.status, AdRequest.Status.PENDING_MANUAL)
        self.assertEqual(ad.category.slug, 'other')

    def test_phone_stored_in_contact_snapshot_and_field(self):
        response = self._submit(phone='+989111111111')
        ad = AdRequest.objects.get(uuid=response.json()['uuid'])
        self.assertEqual(ad.contact_snapshot.get('phone'), '+989111111111')
        self.assertEqual(ad.phone_number, '+989111111111')

    def test_missing_content_returns_400(self):
        response = self._submit(content='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('content', response.json()['error'].lower())

    def test_missing_phone_returns_400(self):
        response = self._submit(phone='')
        self.assertEqual(response.status_code, 400)
        self.assertIn('phone', response.json()['error'].lower())

    def test_invalid_phone_returns_400(self):
        response = self._submit(phone='not-a-number')
        self.assertEqual(response.status_code, 400)

    def test_overlong_content_returns_400(self):
        response = self._submit(content='آ' * 501)
        self.assertEqual(response.status_code, 400)

    def test_unknown_category_falls_back_to_other(self):
        response = self._submit(category='nonexistent-slug-xyz')
        self.assertEqual(response.status_code, 201)
        ad = AdRequest.objects.get(uuid=response.json()['uuid'])
        self.assertEqual(ad.category.slug, 'other')

    def test_get_not_allowed_on_submit_endpoint(self):
        response = self.client.get(reverse('ad_create_submit'))
        self.assertEqual(response.status_code, 405)

    def test_page_template_loads(self):
        from django.template.loader import get_template
        t = get_template('core/ad_create.html')
        self.assertIsNotNone(t)

    def test_page_url_resolves(self):
        from django.urls import resolve
        match = resolve('/ads/create/')
        self.assertEqual(match.url_name, 'ad_create')
