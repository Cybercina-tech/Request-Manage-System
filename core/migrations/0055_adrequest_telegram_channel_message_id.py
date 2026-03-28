# Telegram channel message id for global delete (deleteMessage)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0054_adrequest_phone_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="adrequest",
            name="telegram_channel_message_id",
            field=models.BigIntegerField(
                blank=True,
                help_text="Telegram message_id in the ads channel when posted; used to delete the channel post.",
                null=True,
            ),
        ),
    ]
