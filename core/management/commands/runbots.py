"""
Iraniu — Main bot runtime. Starts supervisor; runs polling or webhook health checker.
Run: python manage.py runbots [options]

- Loads all bots with mode=Polling from DB; for each, clear_webhook(drop_pending_updates=True) then start getUpdates loop.
- Logs "Bot @Username is now polling..." per bot (in worker log and terminal when --once).
- Multi-OS: paths via pathlib.
"""

import os
import signal
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import TelegramBot
from core.services.bot_runner import BotRunnerManager, run_bots_supervisor


class Command(BaseCommand):
    help = (
        "Run bot supervisor: start/restart workers for active bots. "
        "Use --once for single tick; --bot-id to run specific bot(s)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--log-dir",
            type=str,
            default="",
            help="Directory for bot_<id>.log files (default: logs under project root)",
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run single supervisor tick and exit",
        )
        parser.add_argument(
            "--bot-id",
            type=int,
            action="append",
            dest="bot_ids",
            help="Run only these bot ID(s). Can repeat: --bot-id=1 --bot-id=2",
        )
        parser.add_argument(
            "--debug",
            action="store_true",
            help="Enable debug logging",
        )

    def handle(self, *args, **options):
        log_dir = (options.get("log_dir") or "").strip()
        base = Path(settings.BASE_DIR) if getattr(settings, "BASE_DIR", None) else Path.cwd()
        if not log_dir:
            log_dir = str(base / "logs")
        else:
            log_dir = str(Path(log_dir).resolve())
        # Ensure logs directory exists before any worker starts (prevents path/permission issues)
        Path(log_dir).mkdir(parents=True, exist_ok=True)
        once = options.get("once", False)
        bot_ids = options.get("bot_ids") or None
        debug = options.get("debug", False)

        mode = getattr(settings, "TELEGRAM_MODE", "polling").lower()
        if mode not in ("polling", "webhook"):
            mode = "polling"
        print("--- DEBUG: TELEGRAM_MODE is", mode, "---")
        self.stdout.write(
            self.style.SUCCESS(
                f"Supervisor starting (mode={mode}) — Ctrl+C to stop"
            )
        )
        qs = TelegramBot.objects.filter(is_active=True, mode=TelegramBot.Mode.POLLING)
        if bot_ids is not None:
            qs = qs.filter(pk__in=bot_ids)
        bots = list(qs.values_list("pk", "username"))
        if bots:
            names = ", ".join(f"@{u}" if u else f"id={pk}" for pk, u in bots)
            self.stdout.write(f"Polling bots: {names} (each will log 'Bot @Username is now polling...')")
            # Record this process PID for each bot so the UI can Stop it
            if bot_ids:
                TelegramBot.objects.filter(pk__in=bot_ids).update(
                    current_pid=os.getpid(),
                    is_running=True,
                )
        else:
            self.stdout.write(self.style.WARNING("No active Polling bots in database."))

        manager_ref = {}

        def sigterm(signum, frame):
            self.stdout.write(self.style.WARNING("SIGTERM received, shutting down"))
            manager = manager_ref.get("manager")
            if manager:
                manager.shutdown()
            sys.exit(0)

        try:
            signal.signal(signal.SIGTERM, sigterm)
            signal.signal(signal.SIGINT, sigterm)
        except (ValueError, OSError):
            pass

        try:
            if once:
                self.stdout.write("Running single tick then exit...")
                manager = BotRunnerManager(log_dir=log_dir)
                manager.run_once(bot_ids=bot_ids, debug=debug)
            else:
                try:
                    run_bots_supervisor(
                        log_dir=log_dir,
                        bot_ids=bot_ids,
                        debug=debug,
                        manager_ref=manager_ref,
                    )
                except KeyboardInterrupt:
                    self.stdout.write(self.style.WARNING("Interrupted"))
                    if manager_ref.get("manager"):
                        manager_ref["manager"].shutdown()
        finally:
            if bot_ids:
                TelegramBot.objects.filter(pk__in=bot_ids).update(
                    current_pid=None,
                    is_running=False,
                )
