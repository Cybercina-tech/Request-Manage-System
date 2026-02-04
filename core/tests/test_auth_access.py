"""
Iranio — Auth lockdown: anonymous cannot access internal pages; staff can.
"""

from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth import get_user_model

User = get_user_model()


class AnonymousAccessTests(TestCase):
    """Anonymous users must be redirected to login for internal URLs."""

    def setUp(self):
        self.client = Client()

    def test_anonymous_cannot_access_dashboard(self):
        response = self.client.get(reverse('dashboard'), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_anonymous_redirected_to_login_from_requests(self):
        response = self.client.get(reverse('ad_list'), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_anonymous_redirected_to_login_from_settings(self):
        response = self.client.get(reverse('settings'), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_anonymous_can_access_landing(self):
        response = self.client.get(reverse('landing'))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_can_access_login(self):
        response = self.client.get(reverse('login'))
        self.assertEqual(response.status_code, 200)

    def test_anonymous_can_post_api_submit(self):
        response = self.client.post(
            reverse('submit_ad'),
            data={'content': 'test ad'},
            content_type='application/json',
        )
        self.assertIn(response.status_code, (200, 400))

    def test_anonymous_cannot_access_approve_api(self):
        response = self.client.post(
            reverse('approve_ad'),
            data={'ad_id': '00000000-0000-0000-0000-000000000000'},
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_anonymous_cannot_access_pulse_api(self):
        response = self.client.get(reverse('api_pulse'))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))


class StaffAccessTests(TestCase):
    """Logged-in staff can access dashboard and staff-only pages."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            username='staffuser',
            password='testpass123',
            is_staff=True,
        )
        self.client.force_login(self.staff)

    def test_staff_can_access_dashboard(self):
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)

    def test_staff_landing_redirects_to_dashboard(self):
        response = self.client.get(reverse('landing'), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard'))

    def test_staff_can_access_requests(self):
        response = self.client.get(reverse('ad_list'))
        self.assertEqual(response.status_code, 200)

    def test_staff_can_access_settings(self):
        response = self.client.get(reverse('settings'))
        self.assertEqual(response.status_code, 200)


class NonStaffAuthenticatedTests(TestCase):
    """Logged-in non-staff users are blocked from staff pages."""

    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='normaluser',
            password='testpass123',
            is_staff=False,
        )
        self.client.force_login(self.user)

    def test_non_staff_cannot_access_dashboard(self):
        response = self.client.get(reverse('dashboard'), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_non_staff_cannot_access_requests(self):
        response = self.client.get(reverse('ad_list'), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(reverse('login')))

    def test_non_staff_landing_redirects_to_dashboard(self):
        response = self.client.get(reverse('landing'), follow=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse('dashboard'))
