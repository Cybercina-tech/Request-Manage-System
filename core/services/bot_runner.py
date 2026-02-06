"""
Iranio — Bot runner manager: start/stop/restart polling workers (multiprocessing.Process).
Supervisor loop: check workers, apply requested_action, restart dead. Persists PID to DB.
"""

import logging
import multiprocessing
import os
import time
from pathlib import Path

from django.utils import timezone

from core.models import TelegramBot
from core.services.bot_worker import run_bot

logger = logging.getLogger(__name__)

SUPERVISOR_INTERVAL = 10
DEFAULT_LOG_DIR = "logs"


class BotRunnerManager:
    """
    Manages one or more bot worker processes. Each bot runs in isolated Process.
    Start/stop/restart update DB (worker_pid, worker_started_at, requested_action).
    """

    def __init__(self, log_dir: str = None):
        self.log_dir = (log_dir or DEFAULT_LOG_DIR).strip() or DEFAULT_LOG_DIR
        self._processes = {}  # bot_id -> Process
        self._shutdown = False

    def start_bot(self, bot_id: int) -> bool:
        """Start polling worker for bot_id. Returns True if started."""
        if bot_id in self._processes and self._processes[bot_id].is_alive():
            return True
        self._stop_bot_process(bot_id)
        try:
            bot = TelegramBot.objects.get(pk=bot_id)
        except TelegramBot.DoesNotExist:
            logger.warning("start_bot: bot_id=%s not found", bot_id)
            return False
        if bot.mode != TelegramBot.Mode.POLLING:
            logger.info("start_bot: bot_id=%s mode=%s, skip polling", bot_id, bot.mode)
            return False
        if not bot.is_active:
            logger.info("start_bot: bot_id=%s inactive", bot_id)
            return False
        p = multiprocessing.Process(target=run_bot, args=(bot_id, self.log_dir), daemon=False)
        p.start()
        self._processes[bot_id] = p
        try:
            TelegramBot.objects.filter(pk=bot_id).update(
                worker_pid=p.pid,
                worker_started_at=timezone.now(),
                requested_action=None,
            )
        except Exception as e:
            logger.exception("start_bot: failed to save worker_pid: %s", e)
        logger.info("start_bot: bot_id=%s pid=%s", bot_id, p.pid)
        return True

    def _stop_bot_process(self, bot_id: int) -> None:
        """Stop process for bot_id if running; clear DB."""
        p = self._processes.pop(bot_id, None)
        if p and p.is_alive():
            p.terminate()
            p.join(timeout=10)
            if p.is_alive():
                p.kill()
                p.join(timeout=2)
        try:
            TelegramBot.objects.filter(pk=bot_id).update(
                worker_pid=None,
                worker_started_at=None,
                requested_action=None,
            )
        except Exception as e:
            logger.debug("_stop_bot_process update: %s", e)

    def stop_bot(self, bot_id: int) -> bool:
        """Stop polling worker. Returns True if stopped or was not running."""
        self._stop_bot_process(bot_id)
        try:
            TelegramBot.objects.filter(pk=bot_id).update(status=TelegramBot.Status.OFFLINE)
        except Exception:
            pass
        return True

    def restart_bot(self, bot_id: int) -> bool:
        """Stop then start. Returns True if start succeeded."""
        self._stop_bot_process(bot_id)
        time.sleep(1)
        return self.start_bot(bot_id)

    def supervisor_tick(self) -> None:
        """
        Check all workers; apply requested_action; restart dead; stop disabled.
        Call every SUPERVISOR_INTERVAL seconds from runbots command.
        """
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
                self.restart_bot(bot_id)
                continue
            if requested_action == TelegramBot.RequestedAction.START:
                if not proc or not proc.is_alive():
                    self.start_bot(bot_id)
                else:
                    try:
                        TelegramBot.objects.filter(pk=bot_id).update(requested_action=None)
                    except Exception:
                        pass
                continue
            if proc and not proc.is_alive():
                self._processes.pop(bot_id, None)
                try:
                    TelegramBot.objects.filter(pk=bot_id).update(
                        worker_pid=None,
                        worker_started_at=None,
                        status=TelegramBot.Status.OFFLINE,
                    )
                except Exception:
                    pass
                self.start_bot(bot_id)
        for bot_id in list(self._processes.keys()):
            if self._shutdown:
                return
            try:
                b = TelegramBot.objects.filter(pk=bot_id).first()
                if not b or not b.is_active or b.mode != TelegramBot.Mode.POLLING:
                    self._stop_bot_process(bot_id)
            except Exception:
                self._stop_bot_process(bot_id)

    def run_supervisor_loop(self) -> None:
        """Blocking loop: supervisor tick every SUPERVISOR_INTERVAL; start bots that should run."""
        logger.info("Supervisor starting, log_dir=%s", self.log_dir)
        try:
            for bot in TelegramBot.objects.filter(is_active=True, mode=TelegramBot.Mode.POLLING):
                if self._shutdown:
                    break
                self.start_bot(bot.pk)
        except Exception as e:
            logger.exception("Supervisor initial start: %s", e)
        while not self._shutdown:
            time.sleep(SUPERVISOR_INTERVAL)
            self.supervisor_tick()

    def shutdown(self) -> None:
        """Stop all workers and exit."""
        self._shutdown = True
        for bot_id in list(self._processes.keys()):
            self._stop_bot_process(bot_id)
        logger.info("Supervisor shutdown complete")
