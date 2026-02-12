# Add @Iraniu_ads_bot for DEV environment so "No active bot found for environment [DEV]" is resolved.

from django.db import migrations

DEV_BOT_NAME = "Iraniu Ads Bot (DEV)"
DEV_BOT_USERNAME = "Iraniu_ads_bot"
DEV_BOT_TOKEN = "7530881715:AAFssZqOBA3r0uTy_wzYa4FVSeHsmZfj-24"


def add_dev_ads_bot(apps, schema_editor):
    TelegramBot = apps.get_model("core", "TelegramBot")
    from core.encryption import encrypt_token

    if TelegramBot.objects.filter(environment="DEV", username__iexact=DEV_BOT_USERNAME).exists():
        return
    bot = TelegramBot(
        name=DEV_BOT_NAME,
        username=DEV_BOT_USERNAME.strip().lstrip("@"),
        bot_token_encrypted=encrypt_token(DEV_BOT_TOKEN),
        environment="DEV",
        is_active=True,
        is_default=False,
        status="offline",
        mode="polling",
    )
    bot.save()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0044_default_iraniu_ads_bot_remove_dev_bot"),
    ]

    operations = [
        migrations.RunPython(add_dev_ads_bot, noop_reverse),
    ]
