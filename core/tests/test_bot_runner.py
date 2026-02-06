"""
Iraniu — Tests for bot runner and worker. Mock Telegram API and Process where needed.
"""

from unittest.mock import patch, MagicMock

from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from core.models import TelegramBot
from core.services.bot_runner import (
    BotRunnerManager,
    _mark_stale_offline,
    _run_webhook_health_check,
)
from core.services.bot_worker import BotWorker

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

    @patch("core.services.bot_runner.multiprocessing.Process", side_effect=_fake_process)
    def test_supervisor_tick_restarts_dead_worker(self, mock_process):
        """When worker process dies, supervisor restarts it."""
        manager = BotRunnerManager(log_dir="logs")
        manager.start_bot(self.bot.pk)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.worker_pid, 9999)
        # Simulate process died
        proc = manager._processes.get(self.bot.pk)
        proc.is_alive = MagicMock(return_value=False)
        manager.supervisor_tick()
        # Should have started new process
        self.bot.refresh_from_db()
        self.assertIsNotNone(self.bot.worker_pid)
        manager.stop_bot(self.bot.pk)

    def test_mark_stale_offline(self):
        """Bots with last_heartbeat > 90s are marked offline."""
        self.bot.status = TelegramBot.Status.ONLINE
        self.bot.last_heartbeat = timezone.now() - timezone.timedelta(seconds=100)
        self.bot.worker_pid = 12345
        self.bot.save()
        _mark_stale_offline()
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, TelegramBot.Status.OFFLINE)
        self.assertIsNone(self.bot.worker_pid)


class BotWorkerTests(TestCase):
    """BotWorker start/stop and invalid token handling."""

    def setUp(self):
        self.bot = TelegramBot.objects.create(
            name="TestBot",
            username="testbot",
            is_active=True,
            mode=TelegramBot.Mode.POLLING,
        )
        self.bot.set_token("123:ABC")
        self.bot.save()

    @patch("core.services.bot_worker.get_me", return_value=(False, None, "Unauthorized"))
    @patch("core.services.bot_worker.delete_webhook")
    def test_worker_sets_error_on_invalid_token(self, mock_delete, mock_get_me):
        """Invalid token: last_error and status ERROR are set."""
        worker = BotWorker(bot_id=self.bot.pk, log_dir="logs")
        ok = worker.start()
        self.assertFalse(ok)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.status, TelegramBot.Status.ERROR)
        self.assertIn("Unauthorized", self.bot.last_error or "")

    @patch("core.services.bot_worker.get_me", return_value=(True, {"username": "test"}, None))
    @patch("core.services.bot_worker.delete_webhook", return_value=(True, None))
    def test_worker_start_succeeds_with_valid_token(self, mock_delete, mock_get_me):
        """Valid token: start returns True, last_error cleared."""
        self.bot.last_error = "Previous error"
        self.bot.save()
        worker = BotWorker(bot_id=self.bot.pk, log_dir="logs")
        ok = worker.start()
        self.assertTrue(ok)
        self.bot.refresh_from_db()
        self.assertEqual(self.bot.last_error, "")

    @patch("core.services.bot_worker.get_me", return_value=(True, {}, None))
    @patch("core.services.bot_worker.delete_webhook", return_value=(True, None))
    @patch("core.services.bot_worker.process_update")
    @patch("core.services.bot_worker.get_updates")
    def test_worker_run_forever_updates_heartbeat(
        self, mock_updates, mock_process, mock_delete, mock_get_me
    ):
        """Worker calls _update_status with last_heartbeat on successful get_updates."""
        update_calls = []

        def capture_update(bot_id, **kwargs):
            update_calls.append((bot_id, kwargs))

        call_count = [0]
        worker_ref = [None]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] > 1 and worker_ref[0]:
                worker_ref[0]._shutdown_requested = True
            return (True, [] if call_count[0] > 1 else [{"update_id": 1}], None)

        mock_updates.side_effect = side_effect
        worker = BotWorker(bot_id=self.bot.pk, log_dir="logs")
        worker_ref[0] = worker

        with patch("core.services.bot_worker._update_status", side_effect=capture_update):
            worker.run_forever()
        # At least one call should include last_heartbeat
        heartbeat_calls = [kw for _, kw in update_calls if "last_heartbeat" in kw]
        self.assertTrue(
            len(heartbeat_calls) >= 1,
            "Worker should update last_heartbeat on successful get_updates",
        )


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


class WebhookHealthCheckTests(TestCase):
    """Webhook mode health checker."""

    @patch("core.services.bot_runner.get_me", return_value=(True, {"username": "wb"}, None))
    @patch("core.services.bot_runner.get_webhook_info")
    def test_webhook_health_check_valid(self, mock_wh, mock_get_me):
        mock_wh.return_value = (
            True,
            {"url": "https://example.com/telegram/webhook/1/"},
            None,
        )
        bot = TelegramBot.objects.create(
            name="WebhookBot",
            username="wb",
            is_active=True,
            mode=TelegramBot.Mode.WEBHOOK,
            webhook_url="https://example.com/telegram/webhook/1/",
        )
        bot.set_token("123:ABC")
        bot.save()
        _run_webhook_health_check(bot)
        bot.refresh_from_db()
        self.assertEqual(bot.status, TelegramBot.Status.ONLINE)
        self.assertEqual(bot.last_error, "")

    @patch("core.services.bot_runner.get_me", return_value=(False, None, "Invalid token"))
    def test_webhook_health_check_invalid_token(self, mock_get_me):
        bot = TelegramBot.objects.create(
            name="WebhookBot",
            is_active=True,
            mode=TelegramBot.Mode.WEBHOOK,
        )
        bot.set_token("bad")
        bot.save()
        _run_webhook_health_check(bot)
        bot.refresh_from_db()
        self.assertEqual(bot.status, TelegramBot.Status.ERROR)
        self.assertIn("Invalid token", bot.last_error or "")


class BotControlEndpointsTests(TestCase):
    """POST /bots/<id>/start/, stop/, restart/ set requested_action."""

    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(
            username="staff", password="test", is_staff=True
        )
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
        self.assertEqual(
            self.bot.requested_action, TelegramBot.RequestedAction.START
        )

    def test_bot_stop_sets_requested_action(self):
        r = self.client.post(reverse("bot_stop", kwargs={"pk": self.bot.pk}))
        self.assertEqual(r.status_code, 200)
        self.bot.refresh_from_db()
        self.assertEqual(
            self.bot.requested_action, TelegramBot.RequestedAction.STOP
        )

    def test_bot_restart_sets_requested_action(self):
        r = self.client.post(
            reverse("bot_restart", kwargs={"pk": self.bot.pk})
        )
        self.assertEqual(r.status_code, 200)
        self.bot.refresh_from_db()
        self.assertEqual(
            self.bot.requested_action, TelegramBot.RequestedAction.RESTART
        )
