"""
Iraniu — Confirm approve/reject flow tests.
Dedicated confirmation pages; no modals.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

from core.models import AdRequest, TelegramBot

User = get_user_model()


class ConfirmApproveTests(TestCase):
    """Staff can approve via confirmation page. Non-staff blocked. Already approved -> redirect."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            username='staffuser',
            password='testpass123',
            is_staff=True,
        )
        self.bot = TelegramBot.objects.create(
            name='TestBot',
            username='testbot',
            status=TelegramBot.Status.ONLINE,
        )
        self.ad = AdRequest.objects.create(
            content='Test ad content',
            status=AdRequest.Status.PENDING_MANUAL,
            category=AdRequest.Category.OTHER,
            bot=self.bot,
        )

    def test_staff_can_get_confirm_approve_page(self):
        self.client.force_login(self.staff)
        url = reverse('confirm_approve', kwargs={'uuid': self.ad.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Confirm Approval', response.content)
        self.assertIn(b'Test ad content', response.content)

    def test_staff_can_post_confirm_approve(self):
        self.client.force_login(self.staff)
        url = reverse('confirm_approve', kwargs={'uuid': self.ad.uuid})
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.status, AdRequest.Status.APPROVED)
        self.assertRedirects(response, reverse('ad_detail', kwargs={'uuid': self.ad.uuid}))

    def test_non_staff_blocked_from_confirm_approve(self):
        user = User.objects.create_user(username='normaluser', password='testpass123', is_staff=False)
        self.client.force_login(user)
        url = reverse('confirm_approve', kwargs={'uuid': self.ad.uuid})
        response = self.client.get(url, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_anonymous_blocked_from_confirm_approve(self):
        url = reverse('confirm_approve', kwargs={'uuid': self.ad.uuid})
        response = self.client.get(url, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_already_approved_redirects_to_detail(self):
        self.ad.status = AdRequest.Status.APPROVED
        self.ad.save()
        self.client.force_login(self.staff)
        url = reverse('confirm_approve', kwargs={'uuid': self.ad.uuid})
        response = self.client.get(url, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('ad_detail', kwargs={'uuid': self.ad.uuid}))

    def test_double_submit_prevented_already_processed(self):
        """Second POST after first approval: ad is already approved, redirect to detail."""
        self.client.force_login(self.staff)
        url = reverse('confirm_approve', kwargs={'uuid': self.ad.uuid})
        self.client.post(url)
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.status, AdRequest.Status.APPROVED)
        response = self.client.post(url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertRedirects(response, reverse('ad_detail', kwargs={'uuid': self.ad.uuid}))


class ConfirmRejectTests(TestCase):
    """Staff can reject via confirmation page. Reason required. Non-staff blocked."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            username='staffuser',
            password='testpass123',
            is_staff=True,
        )
        self.bot = TelegramBot.objects.create(
            name='TestBot',
            username='testbot',
            status=TelegramBot.Status.ONLINE,
        )
        self.ad = AdRequest.objects.create(
            content='Test ad content',
            status=AdRequest.Status.PENDING_MANUAL,
            category=AdRequest.Category.OTHER,
            bot=self.bot,
        )

    def test_staff_can_get_confirm_reject_page(self):
        self.client.force_login(self.staff)
        url = reverse('confirm_reject', kwargs={'uuid': self.ad.uuid})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Confirm Rejection', response.content)
        self.assertIn(b'Test ad content', response.content)
        self.assertIn(b'reason', response.content)

    def test_staff_can_post_confirm_reject_with_reason(self):
        self.client.force_login(self.staff)
        url = reverse('confirm_reject', kwargs={'uuid': self.ad.uuid})
        response = self.client.post(url, {'reason': 'spam'}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.status, AdRequest.Status.REJECTED)
        self.assertIn('Spam', self.ad.rejection_reason)
        self.assertRedirects(response, reverse('ad_detail', kwargs={'uuid': self.ad.uuid}))

    def test_reject_without_reason_shows_error(self):
        self.client.force_login(self.staff)
        url = reverse('confirm_reject', kwargs={'uuid': self.ad.uuid})
        response = self.client.post(url, {'reason': ''})
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'Rejection reason is required', response.content)
        self.ad.refresh_from_db()
        self.assertEqual(self.ad.status, AdRequest.Status.PENDING_MANUAL)

    def test_non_staff_blocked_from_confirm_reject(self):
        user = User.objects.create_user(username='normaluser', password='testpass123', is_staff=False)
        self.client.force_login(user)
        url = reverse('confirm_reject', kwargs={'uuid': self.ad.uuid})
        response = self.client.get(url, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertIn('login', response.url)

    def test_already_rejected_redirects_to_detail(self):
        self.ad.status = AdRequest.Status.REJECTED
        self.ad.save()
        self.client.force_login(self.staff)
        url = reverse('confirm_reject', kwargs={'uuid': self.ad.uuid})
        response = self.client.get(url, follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('ad_detail', kwargs={'uuid': self.ad.uuid}))
