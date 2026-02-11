# Data migration: create Dev Bot if not present (for dual-bot PROD/DEV)

from django.db import migrations


def add_dev_bot(apps, schema_editor):
    TelegramBot = apps.get_model("core", "TelegramBot")
    if TelegramBot.objects.filter(environment="DEV", username="Iraniu_dev_bot").exists():
        return
    from core.encryption import encrypt_token

    bot = TelegramBot(
        name="Dev Bot",
        username="Iraniu_dev_bot",
        environment="DEV",
        is_active=True,
        status="offline",
        mode="polling",
    )
    bot.bot_token_encrypted = encrypt_token("")
    bot.save()


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0027_telegram_bot_environment"),
    ]

    operations = [
        migrations.RunPython(add_dev_bot, noop),
    ]
