import os
import threading
from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _setup_sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        cursor = connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL;')
        cursor.execute('PRAGMA busy_timeout=15000;')


def _run_deferred_startup():
    """
    Deferred thread: the ONLY place that runs initial connectivity check for the default bot.
    Waits 2 seconds to avoid DB/API access during app init (prevents RuntimeWarning).
    Does NOT start the polling loop; that is controlled by TELEGRAM_MODE in start_auto_bot_runner.
    """
    import time
    time.sleep(2)
    try:
        from django.conf import settings
        from core.services.bot_manager import health_check_default_bot
        # Only hit Telegram API from this thread; never during post_migrate.
        if getattr(settings, 'TELEGRAM_MODE', 'webhook').lower() == 'webhook':
            health_check_default_bot()
        # In polling mode the supervisor loop will do health/start workers.
    except Exception:
        pass


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Iraniu'

    def ready(self):
        connection_created.connect(_setup_sqlite_pragmas)
        from core import signals  # noqa: F401 — register post_save on AdRequest for admin notifications
        from django.db.models.signals import post_migrate
        from django.conf import settings

        def _ensure_default_bot(sender, **kwargs):
            if sender.name == 'core':
                try:
                    from core.services.bot_manager import ensure_default_bot
                    ensure_default_bot()
                    # Do NOT call health_check_default_bot here — no Telegram API during migrations.
                except Exception:
                    pass

        post_migrate.connect(_ensure_default_bot, sender=self)
        # Deferred thread: only place that does initial connectivity check (avoids RuntimeWarning).
        threading.Thread(target=_run_deferred_startup, daemon=True).start()
        # Start bot supervisor only when running the runbots command, NOT runserver (avoids DB access
        # during app init and prevents multiple instances / 409 Conflict when runserver is used).
        import sys
        argv = getattr(sys, "argv", []) or []
        if "runserver" in argv:
            pass
        elif getattr(settings, "TELEGRAM_MODE", "webhook").lower() == "polling":
            from core.services.bot_runner import start_auto_bot_runner
            start_auto_bot_runner()
