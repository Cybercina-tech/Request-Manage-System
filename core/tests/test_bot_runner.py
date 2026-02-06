"""
Iranio — Tests for bot runner and worker. Mock Telegram API and Process where needed.
"""

from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse

from core.models import TelegramBot
from core.services.bot_runner import BotRunnerManager

User = get_user_model()


def _fake_process(*args, **kwargs):
    p = MagicMock()
    p.pid = 9999
    p.is_alive = MagicMock(return_value=True)
    p.terminate = MagicMock()
    p.join = MagicMock()
    return p


class BotRunnerManagerTests(TestCase):
    """BotRunnerManager start/stop/restart and supervisor behavior."""

    def setUp(self):
        self.bot = TelegramBot.objects.create(
            name="TestBot",
            username="testbot",
            is_active=True,
            mode=TelegramBot.Mode.POLLING,
        )
        self.bot.set_token("123:ABC")
        self.bot.save()

    @patch("core.services.bot_runner.multiprocessing.Process", side_effect=_fake_process)
    def test_start_bot_creates_process_and_updates_db(self, mock_process):
        manager = BotRunnerManager(log_dir="logs")
        ok = manager.start_bot(self.bot.pk)
        self.assertTrue(ok)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.worker_pid, 9999)
        manager.stop_bot(self.bot.pk)
        self.bot.refresh_from_db()
        self.assertIsNone(self.bot.worker_pid)

    @patch("core.services.bot_runner.multiprocessing.Process", side_effect=_fake_process)
    def test_stop_bot_clears_process_and_db(self, mock_process):
        manager = BotRunnerManager(log_dir="logs")
        manager.start_bot(self.bot.pk)
        self.bot.refresh_from_db()
        self.assertIsNotNone(self.bot.worker_pid)
        manager.stop_bot(self.bot.pk)
        self.bot.refresh_from_db()
        self.assertIsNone(self.bot.worker_pid)
        self.assertNotIn(self.bot.pk, manager._processes)

    @patch("core.services.bot_runner.multiprocessing.Process", side_effect=_fake_process)
    def test_restart_bot_stops_then_starts(self, mock_process):
        manager = BotRunnerManager(log_dir="logs")
        ok1 = manager.start_bot(self.bot.pk)
        ok2 = manager.restart_bot(self.bot.pk)
        self.assertTrue(ok1)
        self.assertTrue(ok2)
        self.bot.refresh_from_db()
        self.assertIsNotNone(self.bot.worker_pid)
        manager.stop_bot(self.bot.pk)

    def test_start_bot_skipped_when_mode_webhook(self):
        self.bot.mode = TelegramBot.Mode.WEBHOOK
        self.bot.save()
        manager = BotRunnerManager(log_dir="logs")
        ok = manager.start_bot(self.bot.pk)
        self.assertFalse(ok)
        self.assertNotIn(self.bot.pk, manager._processes)

    def test_start_bot_skipped_when_inactive(self):
        self.bot.is_active = False
        self.bot.save()
        manager = BotRunnerManager(log_dir="logs")
        ok = manager.start_bot(self.bot.pk)
        self.assertFalse(ok)


class BotWorkerExitTests(TestCase):
    """Worker exits when bot is inactive (no infinite loop)."""

    @patch("core.services.bot_worker.delete_webhook")
    def test_run_bot_exits_when_bot_inactive(self, mock_delete_webhook):
        bot = TelegramBot.objects.create(
            name="InactiveBot",
            username="inactive",
            is_active=False,
            mode=TelegramBot.Mode.POLLING,
        )
        bot.set_token("123:ABC")
        bot.save()
        from core.services.bot_worker import run_bot

        run_bot(bot.pk, log_dir="logs")
        bot.refresh_from_db()
        self.assertIsNone(bot.worker_pid)


class BotControlEndpointsTests(TestCase):
    """POST /bots/<id>/start/, stop/, restart/ set requested_action."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(username="staff", password="test", is_staff=True)
        self.client.force_login(self.staff)
        self.bot = TelegramBot.objects.create(
            name="TestBot",
            username="testbot",
            is_active=True,
            mode=TelegramBot.Mode.POLLING,
        )
        self.bot.set_token("123:ABC")
        self.bot.save()

    def test_bot_start_sets_requested_action(self):
        r = self.client.post(reverse("bot_start", kwargs={"pk": self.bot.pk}))
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data.get("status"), "success")
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.requested_action, TelegramBot.RequestedAction.START)

    def test_bot_stop_sets_requested_action(self):
        r = self.client.post(reverse("bot_stop", kwargs={"pk": self.bot.pk}))
        self.assertEqual(r.status_code, 200)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.requested_action, TelegramBot.RequestedAction.STOP)

    def test_bot_restart_sets_requested_action(self):
        r = self.client.post(reverse("bot_restart", kwargs={"pk": self.bot.pk}))
        self.assertEqual(r.status_code, 200)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.requested_action, TelegramBot.RequestedAction.RESTART)
