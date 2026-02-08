# Default Telegram Bot: is_default flag + idempotent creation

from django.db import migrations, models


def ensure_default_bot(apps, schema_editor):
    TelegramBot = apps.get_model("core", "TelegramBot")
    if TelegramBot.objects.filter(is_default=True).exists():
        return
    from core.encryption import encrypt_token
    TelegramBot.objects.create(
        name="Iraniu Main Bot",
        is_default=True,
        is_active=True,
        bot_token_encrypted=encrypt_token(""),
        status="offline",
        mode="webhook",
    )


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_webhook_secret_token_and_needs_revision"),
    ]

    operations = [
        migrations.AddField(
            model_name="telegrambot",
            name="is_default",
            field=models.BooleanField(db_index=True, default=False, help_text="Only one bot can be default. Used as the system bot when no other is specified."),
        ),
        migrations.RunPython(ensure_default_bot, migrations.RunPython.noop),
    ]
