# Force @Iraniu_ads_bot to webhook mode and ensure token (post-0017 idempotent).
# Run after 0017 so the default bot is always webhook-only.

from django.db import migrations

OFFICIAL_BOT_USERNAME = "Iraniu_ads_bot"
OFFICIAL_BOT_TOKEN = ""


def force_webhook_and_token(apps, schema_editor):
    TelegramBot = apps.get_model("core", "TelegramBot")
    from core.encryption import encrypt_token
    bot = TelegramBot.objects.filter(username=OFFICIAL_BOT_USERNAME).first()
    if not bot:
        return
    TelegramBot.objects.filter(pk=bot.pk).update(
        mode="webhook",
        bot_token_encrypted=encrypt_token(OFFICIAL_BOT_TOKEN),
    )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_iraniu_official_ads_bot"),
    ]
    operations = [
        migrations.RunPython(force_webhook_and_token, noop_reverse),
    ]
