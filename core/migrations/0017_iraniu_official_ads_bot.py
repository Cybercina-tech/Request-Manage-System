# Iraniu Official Ads Bot — permanent default bot with credentials (data migration)
# For production: rotate this token and set via UI or env; do not commit live tokens.

from django.db import migrations


OFFICIAL_BOT_NAME = "Iraniu Official Ads Bot"
OFFICIAL_BOT_USERNAME = "Iraniu_ads_bot"
OFFICIAL_BOT_TOKEN = "8490372835:AAHo_Xlftu-bcCTfP5ogW8zmNTr3cpS8s58"


def create_official_ads_bot(apps, schema_editor):
    TelegramBot = apps.get_model("core", "TelegramBot")
    if TelegramBot.objects.filter(name=OFFICIAL_BOT_NAME).exists():
        return
    from core.encryption import encrypt_token
    TelegramBot.objects.filter(is_default=True).update(is_default=False)
    bot = TelegramBot(
        name=OFFICIAL_BOT_NAME,
        username=OFFICIAL_BOT_USERNAME.strip().lstrip("@"),
        is_default=True,
        is_active=True,
        bot_token_encrypted=encrypt_token(OFFICIAL_BOT_TOKEN),
        status="offline",
        mode="webhook",
    )
    bot.save()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_telegrambot_is_default"),
    ]

    operations = [
        migrations.RunPython(create_official_ads_bot, noop_reverse),
    ]
