"""
Iraniu — Bot runner manager: start/stop/restart polling workers.
Supports polling (getUpdates) and webhook (health checker only) modes.

Provides run_bots_supervisor() for use by the runbots management command and
by the auto-start runner (AppConfig.ready) so logic is not duplicated.
"""

import atexit
import logging
import multiprocessing
import os
import signal
import sys
import threading
import time

from django.conf import settings
from django.utils import timezone

from core.models import TelegramBot
from core.services.bot_worker import run_bot
from core.services.telegram_client import get_webhook_info, set_webhook, get_me

logger = logging.getLogger(__name__)

SUPERVISOR_INTERVAL = 10
HEARTBEAT_CHECK_INTERVAL = 30
OFFLINE_THRESHOLD_SEC = 90
DEFAULT_LOG_DIR = "logs"


def _mark_stale_offline():
    """Mark bots offline if last_heartbeat > OFFLINE_THRESHOLD_SEC."""
    try:
        threshold = timezone.now() - timezone.timedelta(seconds=OFFLINE_THRESHOLD_SEC)
        count = TelegramBot.objects.filter(
            is_active=True,
            status=TelegramBot.Status.ONLINE,
            last_heartbeat__lt=threshold,
        ).update(status=TelegramBot.Status.OFFLINE, worker_pid=None, worker_started_at=None)
        if count:
            logger.info("Marked %s stale bots offline", count)
    except Exception as e:
        logger.exception("mark_stale_offline: %s", e)


def _validate_webhook_bot(bot: TelegramBot) -> bool:
    """Verify webhook is set and HTTPS. Returns True if valid."""
    token = bot.get_decrypted_token()
    if not token:
        return False
    success, info, err = get_webhook_info(token)
    if not success:
        logger.warning("getWebhookInfo failed bot_id=%s: %s", bot.pk, err)
        return False
    url = (info or {}).get("url", "")
    if not url:
        logger.warning("Webhook not set for bot_id=%s", bot.pk)
        return False
    if not url.startswith("https://"):
        logger.warning("Webhook must be HTTPS bot_id=%s url=%s", bot.pk, url[:50])
        return False
    return True


def _run_webhook_health_check(bot: TelegramBot) -> None:
    """Validate token and webhook; update status."""
    try:
        token = bot.get_decrypted_token()
        if not token:
            TelegramBot.objects.filter(pk=bot.pk).update(
                status=TelegramBot.Status.ERROR,
                last_error="No token configured",
            )
            return
        success, _, err = get_me(token)
        if not success:
            TelegramBot.objects.filter(pk=bot.pk).update(
                status=TelegramBot.Status.ERROR,
                last_error=(err or "Invalid token")[:2048],
            )
            return
        if not _validate_webhook_bot(bot):
            TelegramBot.objects.filter(pk=bot.pk).update(
                status=TelegramBot.Status.ERROR,
                last_error="Webhook not set or not HTTPS",
            )
            return
        TelegramBot.objects.filter(pk=bot.pk).update(
            status=TelegramBot.Status.ONLINE,
            last_heartbeat=timezone.now(),
            last_error="",
        )
    except Exception as e:
        logger.exception("webhook_health_check bot_id=%s: %s", bot.pk, e)
        TelegramBot.objects.filter(pk=bot.pk).update(
            status=TelegramBot.Status.ERROR,
            last_error=str(e)[:2048],
        )


class BotRunnerManager:
    """
    Manages bot workers: polling (child processes) or webhook (health checker).
    Respects TELEGRAM_MODE and bot.mode.
    """

    def __init__(self, log_dir: str = None):
        self.log_dir = (log_dir or DEFAULT_LOG_DIR).strip() or DEFAULT_LOG_DIR
        self._processes = {}
        self._shutdown = False
        self._telegram_mode = getattr(
            settings, "TELEGRAM_MODE", "polling"
        ).lower()
        if self._telegram_mode not in ("polling", "webhook"):
            self._telegram_mode = "polling"

    def start_bot(self, bot_id: int, debug: bool = False) -> bool:
        """Start polling worker for bot_id (singleton: one process per bot_id). Returns True if started or already running."""
        proc = self._processes.get(bot_id)
        if proc is not None and proc.is_alive():
            logger.debug("start_bot: bot_id=%s already running pid=%s", bot_id, proc.pid)
            return True
        self._stop_bot_process(bot_id)
        try:
            bot = TelegramBot.objects.get(pk=bot_id)
        except TelegramBot.DoesNotExist:
            logger.warning("start_bot: bot_id=%s not found", bot_id)
            return False
        if bot.mode != TelegramBot.Mode.POLLING:
            logger.info(
                "start_bot: bot_id=%s mode=%s, skip polling", bot_id, bot.mode
            )
            return False
        if not bot.is_active:
            logger.info("start_bot: bot_id=%s inactive", bot_id)
            return False
        p = multiprocessing.Process(
            target=run_bot,
            args=(bot_id, self.log_dir),
            kwargs={"debug": debug},
            daemon=True,
        )
        p.start()
        self._processes[bot_id] = p
        try:
            TelegramBot.objects.filter(pk=bot_id).update(
                worker_pid=p.pid,
                worker_started_at=timezone.now(),
                requested_action=None,
                last_error="",
            )
        except Exception as e:
            logger.exception("start_bot: failed to save worker_pid: %s", e)
        logger.info("start_bot: bot_id=%s pid=%s", bot_id, p.pid)
        return True

    def _stop_bot_process(self, bot_id: int) -> None:
        """Stop process for bot_id if running; clear DB (worker_pid, current_pid, is_running)."""
        p = self._processes.pop(bot_id, None)
        if p and p.is_alive():
            try:
                p.terminate()
                p.join(timeout=10)
                if p.is_alive():
                    p.kill()
                    p.join(timeout=2)
            except (ProcessLookupError, OSError) as e:
                logger.debug("_stop_bot_process terminate bot_id=%s: %s", bot_id, e)
        try:
            TelegramBot.objects.filter(pk=bot_id).update(
                worker_pid=None,
                worker_started_at=None,
                current_pid=None,
                is_running=False,
                requested_action=None,
            )
        except Exception as e:
            logger.debug("_stop_bot_process update: %s", e)

    def stop_bot(self, bot_id: int) -> bool:
        """Stop polling worker."""
        self._stop_bot_process(bot_id)
        try:
            TelegramBot.objects.filter(pk=bot_id).update(
                status=TelegramBot.Status.OFFLINE
            )
        except Exception:
            pass
        return True

    def restart_bot(self, bot_id: int, debug: bool = False) -> bool:
        """Stop then start. Returns True if start succeeded."""
        self._stop_bot_process(bot_id)
        time.sleep(1)
        return self.start_bot(bot_id, debug=debug)

    def supervisor_tick(self, debug: bool = False) -> None:
        """
        Check workers; apply requested_action; restart dead; mark stale offline.
        """
        _mark_stale_offline()
        # Only POLLING bots: WEBHOOK bots are never spawned as polling workers.
        try:
            bots = list(
                TelegramBot.objects.filter(
                    is_active=True,
                    mode=TelegramBot.Mode.POLLING,
                ).values_list("pk", "worker_pid", "requested_action")
            )
        except Exception as e:
            logger.exception("supervisor_tick load bots: %s", e)
            return
        for bot_id, worker_pid, requested_action in bots:
            if self._shutdown:
                return
            proc = self._processes.get(bot_id)
            if requested_action == TelegramBot.RequestedAction.STOP:
                self._stop_bot_process(bot_id)
                continue
            if requested_action == TelegramBot.RequestedAction.RESTART:
                self.restart_bot(bot_id, debug=debug)
                continue
            if requested_action == TelegramBot.RequestedAction.START:
                if not proc or not proc.is_alive():
                    self.start_bot(bot_id, debug=debug)
                else:
                    try:
                        TelegramBot.objects.filter(pk=bot_id).update(
                            requested_action=None
                        )
                    except Exception:
                        pass
                continue
            if proc and not proc.is_alive():
                self._processes.pop(bot_id, None)
                try:
                    TelegramBot.objects.filter(pk=bot_id).update(
                        worker_pid=None,
                        worker_started_at=None,
                        current_pid=None,
                        is_running=False,
                        status=TelegramBot.Status.OFFLINE,
                    )
                except Exception:
                    pass
                self.start_bot(bot_id, debug=debug)
        for bot_id in list(self._processes.keys()):
            if self._shutdown:
                return
            try:
                b = TelegramBot.objects.filter(pk=bot_id).first()
                if not b or not b.is_active or b.mode != TelegramBot.Mode.POLLING:
                    self._stop_bot_process(bot_id)
            except Exception:
                self._stop_bot_process(bot_id)

    def run_supervisor_loop(self, bot_ids: list = None, debug: bool = False) -> None:
        """
        Blocking loop: start polling workers for any bot with mode=POLLING (regardless of
        TELEGRAM_MODE), then tick supervisor; when TELEGRAM_MODE is webhook also run
        webhook health checks for WEBHOOK bots.
        """
        logger.info(
            "Supervisor starting mode=%s log_dir=%s",
            self._telegram_mode,
            self.log_dir,
        )
        # Always start polling workers for bots that are set to Polling in DB
        try:
            qs = TelegramBot.objects.filter(
                is_active=True, mode=TelegramBot.Mode.POLLING
            )
            if bot_ids is not None:
                qs = qs.filter(pk__in=bot_ids)
            for bot in qs:
                if self._shutdown:
                    break
                self.start_bot(bot.pk, debug=debug)
        except Exception as e:
            logger.exception("Supervisor initial start: %s", e)
        last_webhook_check = 0.0
        while not self._shutdown:
            time.sleep(SUPERVISOR_INTERVAL)
            self.supervisor_tick(debug=debug)
            # When in webhook mode, also run health checks for WEBHOOK bots
            if self._telegram_mode == "webhook":
                now = time.time()
                if now - last_webhook_check >= HEARTBEAT_CHECK_INTERVAL:
                    last_webhook_check = now
                    wqs = TelegramBot.objects.filter(
                        is_active=True, mode=TelegramBot.Mode.WEBHOOK
                    )
                    if bot_ids is not None:
                        wqs = wqs.filter(pk__in=bot_ids)
                    for bot in wqs:
                        if self._shutdown:
                            break
                        _run_webhook_health_check(bot)

    def _run_webhook_loop(self, bot_ids: list = None) -> None:
        """Webhook mode: health check only, no polling workers."""
        qs = TelegramBot.objects.filter(
            is_active=True, mode=TelegramBot.Mode.WEBHOOK
        )
        if bot_ids is not None:
            qs = qs.filter(pk__in=bot_ids)
        bots = list(qs)
        if not bots:
            logger.info("No webhook bots to monitor")
            return
        last_check = 0
        while not self._shutdown:
            now = time.time()
            if now - last_check >= HEARTBEAT_CHECK_INTERVAL:
                last_check = now
                for bot in bots:
                    if self._shutdown:
                        break
                    _run_webhook_health_check(bot)
            time.sleep(SUPERVISOR_INTERVAL)

    def run_once(self, bot_ids: list = None, debug: bool = False) -> None:
        """Single tick: start polling workers for POLLING bots, run webhook health check if webhook mode, then tick."""
        logger.info("Supervisor run_once mode=%s", self._telegram_mode)
        _mark_stale_offline()
        try:
            qs = TelegramBot.objects.filter(
                is_active=True, mode=TelegramBot.Mode.POLLING
            )
            if bot_ids is not None:
                qs = qs.filter(pk__in=bot_ids)
            for bot in qs:
                self.start_bot(bot.pk, debug=debug)
        except Exception as e:
            logger.exception("run_once start: %s", e)
        if self._telegram_mode == "webhook":
            wqs = TelegramBot.objects.filter(
                is_active=True, mode=TelegramBot.Mode.WEBHOOK
            )
            if bot_ids is not None:
                wqs = wqs.filter(pk__in=bot_ids)
            for bot in wqs:
                _run_webhook_health_check(bot)
        self.supervisor_tick(debug=debug)

    def shutdown(self) -> None:
        """Stop all workers and exit."""
        self._shutdown = True
        for bot_id in list(self._processes.keys()):
            self._stop_bot_process(bot_id)
        logger.info("Supervisor shutdown complete")


def run_bots_supervisor(
    log_dir=None,
    bot_ids=None,
    debug=False,
    manager_ref=None,
):
    """
    Create a BotRunnerManager and run the supervisor loop (blocking).
    Used by the runbots management command and by the auto-start runner thread.

    :param log_dir: Directory for bot_<id>.log files; default from settings.BASE_DIR/logs.
    :param bot_ids: Optional list of bot IDs to run; None = all active.
    :param debug: Enable debug logging.
    :param manager_ref: Optional dict; if provided, manager_ref['manager'] is set to the
        BotRunnerManager so callers (e.g. signal handlers) can call manager.shutdown().
    """
    from pathlib import Path

    if not log_dir or not str(log_dir).strip():
        base = getattr(settings, "BASE_DIR", None)
        if base is None:
            base = Path(__file__).resolve().parent.parent.parent
        log_dir = str(Path(base) / "logs")
    else:
        log_dir = str(log_dir).strip() or str(Path(settings.BASE_DIR) / "logs")
    Path(log_dir).mkdir(parents=True, exist_ok=True)

    manager = BotRunnerManager(log_dir=log_dir)
    if manager_ref is not None:
        manager_ref["manager"] = manager
    try:
        manager.run_supervisor_loop(bot_ids=bot_ids, debug=debug)
    finally:
        if manager_ref is not None:
            manager_ref.pop("manager", None)


# --- Auto-start runner (singleton, background thread) ---

_auto_lock = threading.Lock()
_auto_started = False
_auto_manager_ref = {}
_auto_thread = None
_AUTO_RESTART_DELAY_SEC = 60


class _StreamToLog:
    """File-like wrapper that writes to Django logger (for stdout/stderr redirect)."""

    def __init__(self, log_func, prefix=""):
        self._log = log_func
        self._prefix = (prefix + " ") if prefix else ""

    def write(self, msg):
        if msg and msg.strip():
            self._log("%s%s", self._prefix, msg.rstrip())

    def flush(self):
        pass


def _should_skip_auto_bots():
    """True if we should not start auto bots (tests, migrations, Passenger, or disabled by env)."""
    if os.environ.get("ENABLE_AUTO_BOTS", "").lower() in ("false", "0", "no", "off"):
        return True
    enabled = getattr(settings, "ENABLE_AUTO_BOTS", None)
    if enabled is not None and not enabled:
        return True
    if os.environ.get("PASSENGER_APP_ENV"):
        return True
    if "Phusion_Passenger" in (os.environ.get("SERVER_SOFTWARE") or ""):
        return True
    argv = getattr(sys, "argv", []) or []
    skip_commands = ("test", "migrate", "makemigrations", "shell", "shell_plus", "flush", "loaddata", "dumpdata")
    if any(c in argv for c in skip_commands):
        return True
    if "runserver" in argv and os.environ.get("RUN_MAIN") != "true":
        return True
    return False


def _shutdown_auto_runner(*_args):
    """Signal/atexit: request supervisor shutdown so the runner thread can exit."""
    manager = _auto_manager_ref.get("manager")
    if manager:
        try:
            manager.shutdown()
        except Exception as e:
            logger.exception("Error shutting down auto bot runner: %s", e)


def _auto_runner_thread_target():
    """Background thread: run supervisor with restart-on-crash and stdout/stderr → logging."""
    runner_logger = logging.getLogger("core.services.bot_runner.auto")
    # Redirect stdout/stderr in this thread to Django logging
    orig_stdout, orig_stderr = sys.stdout, sys.stderr
    sys.stdout = _StreamToLog(runner_logger.info, "stdout")
    sys.stderr = _StreamToLog(runner_logger.warning, "stderr")
    try:
        while True:
            try:
                run_bots_supervisor(
                    log_dir=None,
                    bot_ids=None,
                    debug=False,
                    manager_ref=_auto_manager_ref,
                )
                break
            except Exception as e:
                runner_logger.exception(
                    "Auto bot supervisor loop crashed, restarting in %ss: %s",
                    _AUTO_RESTART_DELAY_SEC,
                    e,
                )
                time.sleep(_AUTO_RESTART_DELAY_SEC)
    finally:
        sys.stdout = orig_stdout
        sys.stderr = orig_stderr


def start_auto_bot_runner():
    """
    Start the bot supervisor in a background thread (singleton).
    Safe to call from AppConfig.ready(). Skips if ENABLE_AUTO_BOTS is False
    or when under Django autoreload (only starts in the real server process).
    """
    global _auto_started, _auto_thread

    if _should_skip_auto_bots():
        logger.debug("Auto bot runner skipped (ENABLE_AUTO_BOTS or runserver reload)")
        return

    with _auto_lock:
        if _auto_started:
            logger.debug("Auto bot runner already started")
            return
        _auto_started = True

    # Register shutdown so SIGTERM/SIGINT and process exit trigger graceful shutdown
    try:
        signal.signal(signal.SIGTERM, _shutdown_auto_runner)
    except (ValueError, OSError):
        pass  # main thread only on some platforms
    try:
        signal.signal(signal.SIGINT, _shutdown_auto_runner)
    except (ValueError, OSError):
        pass
    atexit.register(_shutdown_auto_runner)

    _auto_thread = threading.Thread(
        target=_auto_runner_thread_target,
        name="iraniu-auto-bots",
        daemon=True,
    )
    _auto_thread.start()
    logger.info("Auto bot runner started in background thread (runserver/gunicorn/WSGI)")


def register_webhook_for_bot(bot: TelegramBot):
    """
    Register webhook for a bot. Call when webhook_url is set.
    Returns (success, message).
    """
    if not bot.webhook_url or not bot.webhook_url.startswith("https://"):
        return False, "Webhook URL must be HTTPS"
    token = bot.get_decrypted_token()
    if not token:
        return False, "No token"
    success, err = set_webhook(
        token,
        bot.webhook_url,
        secret_token=bot.webhook_secret or None,
    )
    return success, err or "OK"
