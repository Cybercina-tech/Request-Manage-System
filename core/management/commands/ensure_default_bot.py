"""
Ensure the default Telegram bot exists (idempotent).
Run after deploy or if the default bot was deleted.
"""

from django.core.management.base import BaseCommand

from core.services.bot_manager import ensure_default_bot


class Command(BaseCommand):
    help = "Ensure exactly one default Telegram bot exists (Iraniu Official Ads Bot). Idempotent."

    def handle(self, *args, **options):
        ensure_default_bot()
        self.stdout.write(self.style.SUCCESS("Default bot check done."))
