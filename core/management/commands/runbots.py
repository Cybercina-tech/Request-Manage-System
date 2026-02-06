"""
Iranio — Main bot runtime. Starts supervisor loop; keeps polling workers alive.
Run: python manage.py runbots [--log-dir=logs]
No external supervisors. Bots run in child processes; DB holds worker_pid and requested_action.
"""

import signal
import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from core.services.bot_runner import BotRunnerManager


class Command(BaseCommand):
    help = "Run the bot supervisor: start/restart polling workers for active bots. Use POST /bots/<id>/start/ to request start."

    def add_arguments(self, parser):
        parser.add_argument(
            "--log-dir",
            type=str,
            default="",
            help="Directory for bot_<id>.log files (default: logs under project root)",
        )

    def handle(self, *args, **options):
        log_dir = (options.get("log_dir") or "").strip()
        if not log_dir:
            base = Path(settings.BASE_DIR)
            log_dir = str(base / "logs")
        manager = BotRunnerManager(log_dir=log_dir)

        def sigterm(signum, frame):
            self.stdout.write(self.style.WARNING("SIGTERM received, shutting down"))
            manager.shutdown()
            sys.exit(0)

        signal.signal(signal.SIGTERM, sigterm)
        try:
            self.stdout.write(self.style.SUCCESS("Supervisor starting (Ctrl+C to stop)"))
            manager.run_supervisor_loop()
        except KeyboardInterrupt:
            self.stdout.write(self.style.WARNING("Interrupted"))
            manager.shutdown()
