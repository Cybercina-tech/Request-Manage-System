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
        _patch_django_context_copy()
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


def _patch_django_context_copy():
    """
    Fix Python 3.14 compatibility: copy(super()) in BaseContext.__copy__ can yield
    a super proxy copy that has no 'dicts' attribute. Replace with object.__new__.
    """
    from copy import copy as _copy
    import django.template.context as _ctx

    def _base_context_copy(self):
        duplicate = object.__new__(type(self))
        duplicate.dicts = self.dicts[:]
        return duplicate

    _ctx.BaseContext.__copy__ = _base_context_copy

    # Context adds render_context and other attributes; ensure they are copied
    _Context = _ctx.Context

    def _context_copy(self):
        duplicate = _base_context_copy(self)
        duplicate.render_context = _copy(self.render_context)
        duplicate.autoescape = self.autoescape
        duplicate.use_l10n = self.use_l10n
        duplicate.use_tz = self.use_tz
        duplicate.template_name = self.template_name
        duplicate.template = self.template
        return duplicate

    _Context.__copy__ = _context_copy
