# Set @Iraniu_ads_bot as default bot with token; remove @Iraniu_dev_bot.

from django.db import migrations

OFFICIAL_BOT_NAME = "Iraniu Official Ads Bot"
OFFICIAL_BOT_USERNAME = "Iraniu_ads_bot"
# Token for @Iraniu_ads_bot (set as default; rotate in production if needed)
OFFICIAL_BOT_TOKEN = "7530881715:AAFssZqOBA3r0uTy_wzYa4FVSeHsmZfj-24"


def set_default_ads_bot_and_remove_dev(apps, schema_editor):
    TelegramBot = apps.get_model("core", "TelegramBot")
    from core.encryption import encrypt_token

    # Remove Dev Bot (@Iraniu_dev_bot)
    TelegramBot.objects.filter(username__iexact="Iraniu_dev_bot").delete()

    # Clear default from all bots
    TelegramBot.objects.filter(is_default=True).update(is_default=False)

    # Find or create @Iraniu_ads_bot
    ads_bot = TelegramBot.objects.filter(username__iexact=OFFICIAL_BOT_USERNAME).first()
    if ads_bot:
        ads_bot.bot_token_encrypted = encrypt_token(OFFICIAL_BOT_TOKEN)
        ads_bot.is_default = True
        ads_bot.is_active = True
        ads_bot.save()
    else:
        ads_bot = TelegramBot(
            name=OFFICIAL_BOT_NAME,
            username=OFFICIAL_BOT_USERNAME.strip().lstrip("@"),
            bot_token_encrypted=encrypt_token(OFFICIAL_BOT_TOKEN),
            is_default=True,
            is_active=True,
            environment="PROD",
            status="offline",
            mode="polling",
        )
        ads_bot.save()


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_siteconfiguration_is_instagram_enabled_and_more"),
    ]

    operations = [
        migrations.RunPython(set_default_ads_bot_and_remove_dev, noop_reverse),
    ]
