# Generated for Iraniu

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfiguration',
            name='submission_ack_message',
            field=models.TextField(
                blank=True,
                default="Your broadcast is currently under AI scrutiny. We'll notify you the moment it goes live."
            ),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='telegram_bot_username',
            field=models.CharField(blank=True, help_text='Bot username without @, for Edit & Resubmit link', max_length=64),
        ),
    ]
