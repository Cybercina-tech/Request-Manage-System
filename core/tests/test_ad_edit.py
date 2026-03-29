"""
Iraniu — Tests for the ad_request_update (Edit Ad) endpoint.
Staff can edit content/category/status/phone via JSON POST; anonymous is blocked;
validation errors return 400.
"""

import json

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

from core.models import AdRequest, Category, TelegramBot, TelegramUser

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['*'])
class AdRequestUpdateTests(TestCase):
    """POST /requests/<uuid>/update/ — staff-only JSON endpoint."""

    def setUp(self):
        self.http = Client()
        self.staff = User.objects.create_user(
            username='staffuser', password='testpass123', is_staff=True,
        )
        self.bot = TelegramBot.objects.create(
            name='TestBot', username='testbot', status=TelegramBot.Status.ONLINE,
        )
        self.category = Category.objects.get(slug='other')
        self.ad = AdRequest.objects.create(
            content='Original content',
            status=AdRequest.Status.PENDING_MANUAL,
            category=self.category,
            phone_number='+989111111111',
            bot=self.bot,
        )
        self.url = reverse('ad_request_update', kwargs={'uuid': self.ad.uuid})

    def _post_json(self, data):
        return self.http.post(
            self.url,
            data=json.dumps(data),
            content_type='application/json',
        )

    # --- success ---

    def test_staff_can_edit_content(self):
        self.http.force_login(self.staff)
        resp = self._post_json({
            'content': 'Updated content',
            'status': 'pending_manual',
            'category': self.category.pk,
            'phone_number': '+989222222222',
        })
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['status'], 'success')
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.content, 'Updated content')
        self.assertEqual(self.ad.phone_number, '+989222222222')

    def test_edit_changes_status(self):
        self.http.force_login(self.staff)
        self._post_json({
            'content': 'Updated',
            'status': 'approved',
            'category': self.category.pk,
        })
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.status, AdRequest.Status.APPROVED)

    def test_edit_changes_category(self):
        self.http.force_login(self.staff)
        new_cat = Category.objects.exclude(slug='other').filter(is_active=True).first()
        if not new_cat:
            self.skipTest('Only one active category in test DB')
        self._post_json({
            'content': 'Updated',
            'status': 'pending_manual',
            'category': new_cat.pk,
        })
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.category_id, new_cat.pk)

    def test_phone_synced_to_telegram_user(self):
        tu = TelegramUser.objects.create(
            telegram_user_id=123456, phone_number='+989000000000',
        )
        self.ad.user = tu
        self.ad.save(update_fields=['user'])
        self.http.force_login(self.staff)
        self._post_json({
            'content': 'Updated',
            'status': 'pending_manual',
            'phone_number': '+989333333333',
        })
        tu.refresh_from_db()
        self.assertEqual(tu.phone_number, '+989333333333')

    # --- validation errors ---

    def test_empty_content_returns_400(self):
        self.http.force_login(self.staff)
        resp = self._post_json({
            'content': '',
            'status': 'pending_manual',
        })
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertIn('content', data.get('errors', {}))

    def test_content_too_long_returns_400(self):
        self.http.force_login(self.staff)
        resp = self._post_json({
            'content': 'x' * 600,
            'status': 'pending_manual',
        })
        self.assertEqual(resp.status_code, 400)

    # --- permission ---

    def test_anonymous_blocked(self):
        resp = self._post_json({'content': 'hack', 'status': 'approved'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.url)

    def test_non_staff_blocked(self):
        user = User.objects.create_user(
            username='normaluser', password='testpass123', is_staff=False,
        )
        self.http.force_login(user)
        resp = self._post_json({'content': 'hack', 'status': 'approved'})
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.url)

    def test_get_method_not_allowed(self):
        self.http.force_login(self.staff)
        resp = self.http.get(self.url)
        self.assertEqual(resp.status_code, 405)
