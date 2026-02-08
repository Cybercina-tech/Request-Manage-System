"""
Reconfigure webhook for the default Telegram bot.
Fetches the bot with is_default=True, calls deleteWebhook then setWebhook
using SiteConfiguration.production_base_url.
"""

from django.core.management.base import BaseCommand

from core.models import TelegramBot, SiteConfiguration
from core.services.bot_manager import activate_webhook


class Command(BaseCommand):
    help = "Reconfigure webhook for the default bot: deleteWebhook then setWebhook with production_base_url."

    def handle(self, *args, **options):
        bot = TelegramBot.objects.filter(is_default=True).first()
        if not bot:
            self.stdout.write(self.style.ERROR("No default bot found. Run ensure_default_bot or create one in the dashboard."))
            return
        config = SiteConfiguration.get_config()
        base = (config.production_base_url or "").strip()
        if not base or not base.startswith("https://"):
            msg = "Set production_base_url in Settings (HTTPS). Current: %s" % (base or "(empty)")
            self.stdout.write(self.style.WARNING(msg))
            return
        success, message, url = activate_webhook(bot)
        if success:
            self.stdout.write(self.style.SUCCESS("Webhook reconfigured: %s" % message))
            if url:
                self.stdout.write("URL: %s" % url)
        else:
            self.stdout.write(self.style.ERROR("Webhook reconfigure failed: %s" % (message or "Unknown error")))
