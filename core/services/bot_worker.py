"""
Iranio — Polling worker: long-running process for one bot. getUpdates loop, no busy-wait.
Runs in isolated process started by BotRunnerManager. Graceful SIGTERM shutdown.
"""

import logging
import os
import signal
import sys
import time

# Ensure Django is available when run as multiprocessing target
if os.environ.get("DJANGO_SETTINGS_MODULE") is None:
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "iranio.settings")
try:
    import django
    django.setup()
except Exception:
    pass

from django.utils import timezone

from core.models import TelegramBot
from core.services.telegram_client import get_updates, delete_webhook
from core.services.telegram_update_handler import process_update

# Per-bot logger; caller can replace with file handler
logger = logging.getLogger(__name__)

POLL_TIMEOUT = 25
RECONNECT_DELAY = 5
HEARTBEAT_INTERVAL_SEC = 30
MAX_CONSECUTIVE_ERRORS = 10


def _setup_bot_logger(bot_id: int, log_dir: str):
    """Add TimedRotatingFileHandler for this bot to a dedicated logger."""
    try:
        os.makedirs(log_dir, exist_ok=True)
        from logging.handlers import TimedRotatingFileHandler
        log_file = os.path.join(log_dir, f"bot_{bot_id}.log")
        handler = TimedRotatingFileHandler(log_file, when="midnight", backupCount=7, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        bot_logger = logging.getLogger(f"core.bot_worker.{bot_id}")
        bot_logger.setLevel(logging.INFO)
        bot_logger.addHandler(handler)
        bot_logger.propagate = False
        return bot_logger
    except Exception as e:
        logging.getLogger(__name__).warning("Could not create bot log file bot_id=%s: %s", bot_id, e)
        return logger


def run_bot(bot_id: int, log_dir: str = "logs") -> None:
    """
    Long-polling loop for one bot. Loads bot from DB, deletes webhook, getUpdates loop.
    Updates heartbeat; handles SIGTERM for clean exit. No busy-wait (uses Telegram timeout).
    """
    bot_log = _setup_bot_logger(bot_id, log_dir)
    bot_log.info("Worker starting bot_id=%s pid=%s", bot_id, os.getpid())

    shutdown_requested = False

    def _sigterm(_signum, _frame):
        nonlocal shutdown_requested
        shutdown_requested = True
        bot_log.info("SIGTERM received, shutting down")

    signal.signal(signal.SIGTERM, _sigterm)

    try:
        bot = TelegramBot.objects.filter(pk=bot_id).first()
        if not bot or not bot.is_active:
            bot_log.warning("Bot %s not found or inactive", bot_id)
            return
        token = bot.get_decrypted_token()
        if not token:
            bot_log.error("Bot %s has no token", bot_id)
            return
        delete_webhook(token)
        bot_log.info("Webhook cleared for polling")
    except Exception as e:
        bot_log.exception("Startup failed: %s", e)
        return

    offset = None
    consecutive_errors = 0
    last_heartbeat = time.monotonic()

    while not shutdown_requested:
        try:
            success, updates, error = get_updates(token, offset=offset, timeout=POLL_TIMEOUT)
            if shutdown_requested:
                break
            if not success:
                consecutive_errors += 1
                bot_log.warning("getUpdates failed (%s): %s", consecutive_errors, error)
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    bot_log.error("Too many errors, exiting")
                    break
                time.sleep(RECONNECT_DELAY)
                continue
            consecutive_errors = 0
            if updates:
                for update in updates:
                    if shutdown_requested:
                        break
                    uid = update.get("update_id")
                    if uid is not None:
                        offset = uid + 1
                    try:
                        bot.refresh_from_db()
                        if not bot.is_active:
                            shutdown_requested = True
                            break
                        process_update(bot, update)
                    except Exception as e:
                        bot_log.exception("process_update failed update_id=%s: %s", uid, e)
                try:
                    TelegramBot.objects.filter(pk=bot_id).update(
                        last_heartbeat=timezone.now(),
                        status=TelegramBot.Status.ONLINE,
                    )
                except Exception as e:
                    bot_log.debug("heartbeat update failed: %s", e)
            now = time.monotonic()
            if now - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
                try:
                    TelegramBot.objects.filter(pk=bot_id).update(
                        last_heartbeat=timezone.now(),
                        status=TelegramBot.Status.ONLINE,
                    )
                    last_heartbeat = now
                except Exception as e:
                    bot_log.debug("heartbeat update failed: %s", e)
        except Exception as e:
            consecutive_errors += 1
            bot_log.exception("Loop error: %s", e)
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                break
            time.sleep(RECONNECT_DELAY)

    try:
        TelegramBot.objects.filter(pk=bot_id).update(
            worker_pid=None,
            worker_started_at=None,
            status=TelegramBot.Status.OFFLINE,
        )
    except Exception as e:
        bot_log.debug("Cleanup update failed: %s", e)
    bot_log.info("Worker exiting bot_id=%s", bot_id)
