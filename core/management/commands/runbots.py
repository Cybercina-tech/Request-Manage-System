"""
Iraniu — Main bot runtime. Starts supervisor; runs polling or webhook health checker.
Run: python manage.py runbots [options]

- Loads all bots with mode=Polling from DB; for each, clear_webhook(drop_pending_updates=True) then start getUpdates loop.
- Logs "Bot @Username is now polling..." per bot (in worker log and terminal when --once).
- Multi-OS: paths via pathlib.
- File lock prevents duplicate execution (works across processes; cache is process-local with LocMem).
"""

import os
import signal
import sys
import time
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.core.management.base import BaseCommand

from core.models import TelegramBot
from core.services.bot_runner import BotRunnerManager, run_bots_supervisor

# File lock so only one runbots process runs (avoids Telegram 409 when two processes use same token).
RUNBOTS_LOCK_FILENAME = "runbots.lock"


def _acquire_file_lock(base_dir: Path):
    """Acquire exclusive file lock under base_dir/logs/runbots.lock. Returns (lock_path, fd) or (None, error_msg)."""
    log_dir = base_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    lock_path = log_dir / RUNBOTS_LOCK_FILENAME
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o644)
    except OSError as e:
        return None, f"Cannot create lock file: {e}"
    try:
        if os.name == "nt":
            import msvcrt
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            except OSError:
                try:
                    with open(lock_path, "r") as f:
                        existing_pid = f.read().strip()
                except Exception:
                    existing_pid = "?"
                os.close(fd)
                return None, f"Another runbots process is running (lock file PID: {existing_pid}). Stop it first to avoid Telegram 409 conflict."
        else:
            import fcntl
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                try:
                    with open(lock_path, "r") as f:
                        existing_pid = f.read().strip()
                except Exception:
                    existing_pid = "?"
                os.close(fd)
                return None, f"Another runbots process is running (lock file PID: {existing_pid}). Stop it first to avoid Telegram 409 conflict."
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode())
        return ((lock_path, fd), None)
    except Exception as e:
        try:
            os.close(fd)
        except Exception:
            pass
        return (None, str(e))


def _release_file_lock(lock_handle):
    """Release file lock. lock_handle is (lock_path, fd) or None."""
    if not lock_handle:
        return
    lock_path, fd = lock_handle
    try:
        if os.name != "nt":
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        try:
            lock_path.unlink(missing_ok=True)
        except Exception:
            pass
    except Exception:
        pass


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
        bot_ids = None  # Defined at start so finally block never sees UnboundLocalError
        current_pid = os.getpid()
        base_dir = Path(settings.BASE_DIR)

        # File lock: only one runbots process system-wide (cache is process-local with LocMem)
        file_lock_handle, file_lock_err = _acquire_file_lock(base_dir)
        if file_lock_err:
            self.stdout.write(self.style.ERROR(file_lock_err))
            sys.exit(1)

        LOCK_KEY = "runbots_execution_lock"
        LOCK_TIMEOUT = 3600
        lock_acquired = cache.add(LOCK_KEY, current_pid, timeout=LOCK_TIMEOUT)

        try:
            log_dir = (options.get("log_dir") or "").strip()
            base = Path(settings.BASE_DIR)
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

            self.stdout.write(
                self.style.SUCCESS(
                    f"Supervisor starting (mode={mode}, PID={current_pid}) — Ctrl+C to stop"
                )
            )

            env = getattr(settings, "ENVIRONMENT", "PROD")
            qs = TelegramBot.objects.filter(
                environment=env, is_active=True, mode=TelegramBot.Mode.POLLING
            )
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
                cache.delete(LOCK_KEY)  # Release lock on signal
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
                    self.stdout.write(self.style.SUCCESS("Single tick completed."))
                else:
                    self.stdout.write("Starting supervisor loop (use Ctrl+C to stop)...")
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
            except Exception:
                # Re-raise exceptions; finally block will handle cleanup
                raise
        finally:
            # Release file lock and cache lock; mark system status as inactive
            _release_file_lock(file_lock_handle)
            cache.delete(LOCK_KEY)
            try:
                from core.models import SystemStatus
                status = SystemStatus.get_status()
                status.is_bot_active = False
                status.save(update_fields=["is_bot_active", "updated_at"])
            except Exception:
                pass
            self.stdout.write(self.style.SUCCESS(f"runbots process exiting (PID={current_pid})"))
            if bot_ids is not None and bot_ids:
                TelegramBot.objects.filter(pk__in=bot_ids).update(
                    current_pid=None,
                    is_running=False,
                )
