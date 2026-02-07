"""
Iraniu — Main bot runtime. Starts supervisor; runs polling or webhook health checker.
Run: python manage.py runbots [options]

No external supervisors. Bots run in child processes; DB holds worker_pid, heartbeat.
Uses run_bots_supervisor() from core.services.bot_runner so logic is shared with
auto-start (AppConfig.ready).
"""

import signal
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

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
        if not log_dir:
            base = Path(settings.BASE_DIR)
            log_dir = str(base / "logs")
        once = options.get("once", False)
        bot_ids = options.get("bot_ids") or None
        debug = options.get("debug", False)

        mode = getattr(settings, "TELEGRAM_MODE", "polling")
        self.stdout.write(
            self.style.SUCCESS(
                f"Supervisor starting (mode={mode}) — Ctrl+C to stop"
            )
        )

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
