from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Iraniu'

    def ready(self):
        from core.services.bot_runner import start_auto_bot_runner
        start_auto_bot_runner()
