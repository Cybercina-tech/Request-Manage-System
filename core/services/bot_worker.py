"""
Iraniu — BotWorker: single-bot worker with start/stop/run_forever.
Handles polling loop, heartbeat, error tracking. Used by runbots command.
Multi-OS: uses pathlib/os for paths; no hardcoded separators.
"""

import logging
import os
import signal
import time
from pathlib import Path

# Ensure Django is available when run as multiprocessing target
if os.environ.get("DJANGO_SETTINGS_MODULE") is None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "iraniu.settings")
try:
    import django
    django.setup()
except Exception:
    pass

from django.utils import timezone

from core.models import TelegramBot
from core.bot_handler import initialize_for_polling
from core.services.telegram_client import get_updates
from core.services.telegram_update_handler import process_update

logger = logging.getLogger(__name__)

POLL_TIMEOUT = 30
RECONNECT_DELAY_BASE = 2
RECONNECT_DELAY_MAX = 60
HEARTBEAT_INTERVAL_SEC = 30
SYSTEM_STATUS_HEARTBEAT_SEC = 60  # Update SystemStatus singleton every 60s for watchdog
MAX_CONSECUTIVE_ERRORS = 15
OFFLINE_THRESHOLD_SEC = 90
# 409 Conflict = another getUpdates already running; backoff and do not retry immediately
CONFLICT_BACKOFF_SEC = (5, 10, 20, 40, 60)
CONFLICT_BACKOFF_MAX_INDEX = len(CONFLICT_BACKOFF_SEC) - 1


def _setup_bot_logger(bot_id: int, log_dir: str):
    """Add TimedRotatingFileHandler for this bot. log_dir: path as str (OS-agnostic)."""
    try:
        log_path = Path(log_dir) if log_dir else Path("logs")
        log_path.mkdir(parents=True, exist_ok=True)
        from logging.handlers import TimedRotatingFileHandler
        log_file = str(log_path / f"bot_{bot_id}.log")
        handler = TimedRotatingFileHandler(
            log_file, when="midnight", backupCount=7, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
        )
        bot_logger = logging.getLogger(f"core.bot_worker.{bot_id}")
        bot_logger.setLevel(logging.INFO)
        bot_logger.addHandler(handler)
        bot_logger.propagate = False
        return bot_logger
    except Exception as e:
        logger.warning(
            "Could not create bot log file bot_id=%s: %s", bot_id, e
        )
        return logger


def _update_status(bot_id: int, **kwargs):
    """Update bot status in DB; never raises."""
    try:
        TelegramBot.objects.filter(pk=bot_id).update(**kwargs)
    except Exception as e:
        logger.debug("update_status failed bot_id=%s: %s", bot_id, e)


def _update_error(bot_id: int, error_msg: str, status: str = None):
    """Store last_error and optionally status."""
    upd = {"last_error": (error_msg or "")[:2048]}
    if status is not None:
        upd["status"] = status
    _update_status(bot_id, **upd)


def _update_system_status_heartbeat():
    """Update SystemStatus singleton (last_heartbeat, is_bot_active=True). Used by runbots worker."""
    try:
        from django.core.cache import cache
        from core.models import SystemStatus
        status = SystemStatus.get_status()
        status.last_heartbeat = timezone.now()
        status.is_bot_active = True
        status.save(update_fields=["last_heartbeat", "is_bot_active", "updated_at"])
        cache.delete("system_status_worker_online")
    except Exception as e:
        logger.debug("update_system_status_heartbeat: %s", e)


class BotWorker:
    """
    Single-bot worker: validates token, polls getUpdates, processes via ConversationEngine.
    Handles network failures, rate limits, invalid tokens. Exposes start/stop/run_forever.
    """

    def __init__(self, bot_id: int, log_dir: str = "logs", debug: bool = False):
        self.bot_id = bot_id
        self.log_dir = log_dir or "logs"
        self.debug = debug
        self._running = False
        self._shutdown_requested = False
        self._log = logger

    def _get_logger(self):
        if self._log is logger:
            self._log = _setup_bot_logger(self.bot_id, self.log_dir)
        return self._log

    def start(self) -> bool:
        """Prepare worker: validate token, clear webhook (drop_pending_updates), then ready for polling."""
        self._running = True
        self._shutdown_requested = False
        bot_log = self._get_logger()
        bot_log.info("Worker starting bot_id=%s pid=%s", self.bot_id, os.getpid())

        try:
            bot = TelegramBot.objects.filter(pk=self.bot_id).first()
            if not bot or not bot.is_active:
                bot_log.warning("Bot %s not found or inactive", self.bot_id)
                return False
            token = bot.get_decrypted_token()
            if not token:
                bot_log.error("Bot %s has no token", self.bot_id)
                _update_error(self.bot_id, "No token configured", TelegramBot.Status.ERROR)
                return False
            success, err, username = initialize_for_polling(token)
            if not success:
                bot_log.error("Invalid token bot_id=%s: %s", self.bot_id, err)
                _update_error(self.bot_id, err or "Invalid token", TelegramBot.Status.ERROR)
                return False
            _update_status(self.bot_id, last_error="")
            display_name = f"@{username}" if username else f"bot_id={self.bot_id}"
            bot_log.info("Bot %s is now polling...", display_name)
            return True
        except Exception as e:
            bot_log.exception("Startup failed bot_id=%s: %s", self.bot_id, e)
            _update_error(self.bot_id, str(e), TelegramBot.Status.ERROR)
            return False

    def stop(self) -> None:
        """Request graceful shutdown."""
        self._shutdown_requested = True

    def run_forever(self) -> None:
        """
        Main polling loop. Runs until stop() is called or fatal error.
        Updates heartbeat every HEARTBEAT_INTERVAL_SEC; stores last_error on failure.
        """
        bot_log = self._get_logger()
        if not self.start():
            return

        token = None
        try:
            bot = TelegramBot.objects.filter(pk=self.bot_id).first()
            if not bot or not bot.is_active:
                return
            token = bot.get_decrypted_token()
        except Exception:
            pass
        if not token:
            return

        offset = None
        consecutive_errors = 0
        conflict_backoff_index = 0
        last_heartbeat_time = time.monotonic()
        last_system_heartbeat_time = time.monotonic()

        def _sigterm(_signum, _frame):
            self._shutdown_requested = True
            bot_log.info("SIGTERM received, shutting down")

        signal.signal(signal.SIGTERM, _sigterm)

        while self._running and not self._shutdown_requested:
            try:
                success, updates, error = get_updates(
                    token, offset=offset, timeout=POLL_TIMEOUT
                )
                if self._shutdown_requested:
                    break
                if not success:
                    err_str = (error or "") if error else ""
                    # 401/Unauthorized = invalid token; exit loop to avoid flooding logs
                    is_401 = "401" in err_str or "unauthorized" in err_str.lower()
                    if is_401:
                        bot_log.warning(
                            "Bot %s: invalid token (401 Unauthorized). Update token in Bots page and restart.",
                            self.bot_id,
                        )
                        _update_error(
                            self.bot_id,
                            "Invalid or expired token (401 Unauthorized). Update token in Bots page.",
                            TelegramBot.Status.ERROR,
                        )
                        break
                    is_409 = "409" in err_str or "conflict" in err_str.lower()
                    if is_409:
                        delay_sec = CONFLICT_BACKOFF_SEC[min(conflict_backoff_index, CONFLICT_BACKOFF_MAX_INDEX)]
                        bot_log.warning(
                            "getUpdates 409 Conflict (multiple instances?); backoff %ss (index=%s)",
                            delay_sec,
                            conflict_backoff_index,
                        )
                        _update_error(
                            self.bot_id,
                            "409 Conflict: another polling instance may be running. Backoff %ss." % delay_sec,
                            None,
                        )
                        time.sleep(delay_sec)
                        if conflict_backoff_index < CONFLICT_BACKOFF_MAX_INDEX:
                            conflict_backoff_index += 1
                        continue
                    consecutive_errors += 1
                    bot_log.warning(
                        "getUpdates failed (%s/%s): %s",
                        consecutive_errors,
                        MAX_CONSECUTIVE_ERRORS,
                        err_str[:200],
                    )
                    _update_error(
                        self.bot_id,
                        error or "getUpdates failed",
                        TelegramBot.Status.ERROR if consecutive_errors >= MAX_CONSECUTIVE_ERRORS else None,
                    )
                    if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        bot_log.error("Too many errors, exiting")
                        break
                    delay = min(
                        RECONNECT_DELAY_BASE * (2 ** (consecutive_errors - 1)),
                        RECONNECT_DELAY_MAX,
                    )
                    time.sleep(delay)
                    continue

                consecutive_errors = 0
                conflict_backoff_index = 0
                _update_status(
                    self.bot_id,
                    last_heartbeat=timezone.now(),
                    status=TelegramBot.Status.ONLINE,
                    last_error="",
                )

                if updates:
                    for update in updates:
                        if self._shutdown_requested:
                            break
                        uid = update.get("update_id")
                        if uid is not None:
                            offset = uid + 1
                        try:
                            bot = TelegramBot.objects.filter(pk=self.bot_id).first()
                            if not bot or not bot.is_active:
                                self._shutdown_requested = True
                                break
                            process_update(bot, update)
                        except Exception as e:
                            bot_log.exception(
                                "process_update failed update_id=%s: %s", uid, e
                            )
                            _update_error(self.bot_id, str(e)[:2048])

                now = time.monotonic()
                if now - last_heartbeat_time >= HEARTBEAT_INTERVAL_SEC:
                    try:
                        _update_status(
                            self.bot_id,
                            last_heartbeat=timezone.now(),
                            status=TelegramBot.Status.ONLINE,
                        )
                        last_heartbeat_time = now
                    except Exception:
                        pass
                if now - last_system_heartbeat_time >= SYSTEM_STATUS_HEARTBEAT_SEC:
                    try:
                        _update_system_status_heartbeat()
                        last_system_heartbeat_time = now
                    except Exception:
                        pass

            except Exception as e:
                consecutive_errors += 1
                err_msg = str(e)[:2048] if e else "Unknown error"
                bot_log.error("Loop error bot_id=%s: %s", self.bot_id, err_msg)
                _update_error(self.bot_id, err_msg, TelegramBot.Status.ERROR)
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    break
                delay = min(
                    RECONNECT_DELAY_BASE * (2 ** (consecutive_errors - 1)),
                    RECONNECT_DELAY_MAX,
                )
                time.sleep(delay)

        self._running = False
        try:
            _update_status(
                self.bot_id,
                worker_pid=None,
                worker_started_at=None,
                status=TelegramBot.Status.OFFLINE,
            )
        except Exception:
            pass
        bot_log.info("Worker exiting bot_id=%s", self.bot_id)


def run_bot(bot_id: int, log_dir: str = "logs", debug: bool = False) -> None:
    """
    Entry point for multiprocessing.Process target.
    Creates BotWorker and runs run_forever().
    """
    worker = BotWorker(bot_id=bot_id, log_dir=log_dir, debug=debug)
    worker.run_forever()
