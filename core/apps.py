from django.apps import AppConfig
from django.db.backends.signals import connection_created


def _setup_sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        cursor = connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL;')
        cursor.execute('PRAGMA busy_timeout=15000;')


class CoreConfig(AppConfig):
    """
    Core app configuration. NO automatic bot starting — all bots must be started
    manually via 'python manage.py runbots' (typically via Cron Job).
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Iraniu'

    def ready(self):
        """
        Setup signals and SQLite pragmas only. NO background threads or bot processes.
        All bot execution is manual via the 'runbots' management command.
        """
        connection_created.connect(_setup_sqlite_pragmas)
        from core import signals  # noqa: F401 — register post_save on AdRequest for admin notifications
        from django.db.models.signals import post_migrate

        def _ensure_default_bot(sender, **kwargs):
            if sender.name == 'core':
                try:
                    from core.services.bot_manager import ensure_default_bot
                    ensure_default_bot()
                except Exception:
                    pass

        post_migrate.connect(_ensure_default_bot, sender=self)
