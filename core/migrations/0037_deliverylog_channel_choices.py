"""Expand DeliveryLog.Channel choices: add telegram_channel and instagram_story."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0036_adtemplate_story_coordinates'),
    ]

    operations = [
        migrations.AlterField(
            model_name='deliverylog',
            name='channel',
            field=models.CharField(
                choices=[
                    ('telegram', 'Telegram'),
                    ('telegram_channel', 'Telegram Channel'),
                    ('instagram', 'Instagram'),
                    ('instagram_story', 'Instagram Story'),
                    ('api', 'API'),
                ],
                db_index=True,
                max_length=24,
            ),
        ),
    ]
