# Set @iraniu_bot as the default bot (PROD + DEV). Replace previous default.

from django.db import migrations

MAIN_BOT_NAME = "Iraniu Bot"
MAIN_BOT_USERNAME = "iraniu_bot"
MAIN_BOT_TOKEN = "7530881715:AAG1MhXljBj4aZXLm7Y5t0HmMKD6pcbLQNI"


def set_iraniu_bot_default(apps, schema_editor):
    TelegramBot = apps.get_model("core", "TelegramBot")
    from core.encryption import encrypt_token

    encrypted = encrypt_token(MAIN_BOT_TOKEN)

    # Clear default from all bots
    TelegramBot.objects.filter(is_default=True).update(is_default=False)

    # PROD: default bot
    prod_bot = TelegramBot.objects.filter(environment="PROD", username__iexact=MAIN_BOT_USERNAME).first()
    if prod_bot:
        prod_bot.bot_token_encrypted = encrypted
        prod_bot.name = MAIN_BOT_NAME
        prod_bot.is_default = True
        prod_bot.is_active = True
        prod_bot.save()
    else:
        TelegramBot.objects.create(
            name=MAIN_BOT_NAME,
            username=MAIN_BOT_USERNAME.strip().lstrip("@"),
            bot_token_encrypted=encrypted,
            environment="PROD",
            is_default=True,
            is_active=True,
            status="offline",
            mode="polling",
        )

    # DEV: same bot so runbots finds an active bot when ENVIRONMENT=DEV
    dev_bot = TelegramBot.objects.filter(environment="DEV", username__iexact=MAIN_BOT_USERNAME).first()
    if dev_bot:
        dev_bot.bot_token_encrypted = encrypted
        dev_bot.name = f"{MAIN_BOT_NAME} (DEV)"
        dev_bot.is_active = True
        dev_bot.save()
    else:
        TelegramBot.objects.create(
            name=f"{MAIN_BOT_NAME} (DEV)",
            username=MAIN_BOT_USERNAME.strip().lstrip("@"),
            bot_token_encrypted=encrypted,
            environment="DEV",
            is_default=False,
            is_active=True,
            status="offline",
            mode="polling",
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0045_add_dev_environment_iraniu_ads_bot"),
    ]

    operations = [
        migrations.RunPython(set_iraniu_bot_default, noop_reverse),
    ]
