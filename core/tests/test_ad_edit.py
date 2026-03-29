"""
Iraniu — Tests for the ad_request_update (Edit Ad) dedicated page.
GET shows pre-filled form; POST saves and redirects to detail.
Staff-only; anonymous/non-staff blocked.
"""

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.contrib.auth import get_user_model

from core.models import AdRequest, Category, TelegramBot, TelegramUser

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False, ALLOWED_HOSTS=['*'])
class AdRequestUpdateTests(TestCase):
    """GET + POST /requests/<uuid>/update/ — dedicated edit page."""

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

    # --- GET: show form ---

    def test_get_shows_edit_page(self):
        self.http.force_login(self.staff)
        resp = self.http.get(self.url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Edit Request')
        self.assertContains(resp, 'Original content')
        self.assertContains(resp, 'Save Changes')
        self.assertContains(resp, 'Cancel')

    def test_get_prefills_phone_from_user(self):
        """When ad.phone_number is empty, form pre-fills from linked TelegramUser."""
        tu = TelegramUser.objects.create(
            telegram_user_id=999001, phone_number='+989000000000',
        )
        self.ad.user = tu
        self.ad.phone_number = ''
        self.ad.save(update_fields=['user', 'phone_number'])
        self.http.force_login(self.staff)
        resp = self.http.get(self.url)
        self.assertContains(resp, '+989000000000')

    # --- POST: save and redirect ---

    def test_post_saves_and_redirects(self):
        self.http.force_login(self.staff)
        resp = self.http.post(self.url, {
            'content': 'Updated content',
            'status': 'pending_manual',
            'category': self.category.pk,
            'phone_number': '+989222222222',
        })
        self.assertRedirects(resp, reverse('ad_detail', kwargs={'uuid': self.ad.uuid}))
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.content, 'Updated content')
        self.assertEqual(self.ad.phone_number, '+989222222222')

    def test_post_changes_status(self):
        self.http.force_login(self.staff)
        self.http.post(self.url, {
            'content': 'Updated',
            'status': 'approved',
            'category': self.category.pk,
        })
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.status, AdRequest.Status.APPROVED)

    def test_post_changes_category(self):
        self.http.force_login(self.staff)
        new_cat = Category.objects.exclude(slug='other').filter(is_active=True).first()
        if not new_cat:
            self.skipTest('Only one active category in test DB')
        self.http.post(self.url, {
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
        self.http.post(self.url, {
            'content': 'Updated',
            'status': 'pending_manual',
            'phone_number': '+989333333333',
        })
        tu.refresh_from_db()
        self.assertEqual(tu.phone_number, '+989333333333')

    # --- validation errors: re-render form ---

    def test_empty_content_shows_errors(self):
        self.http.force_login(self.staff)
        resp = self.http.post(self.url, {
            'content': '',
            'status': 'pending_manual',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Please fix the errors')

    def test_content_too_long_shows_errors(self):
        self.http.force_login(self.staff)
        resp = self.http.post(self.url, {
            'content': 'x' * 600,
            'status': 'pending_manual',
        })
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Please fix the errors')

    # --- permission ---

    def test_anonymous_blocked(self):
        resp = self.http.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.url)

    def test_non_staff_blocked(self):
        user = User.objects.create_user(
            username='normaluser', password='testpass123', is_staff=False,
        )
        self.http.force_login(user)
        resp = self.http.get(self.url)
        self.assertEqual(resp.status_code, 302)
        self.assertIn('login', resp.url)
