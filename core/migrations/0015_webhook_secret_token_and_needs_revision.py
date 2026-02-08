# Generated manually for webhook migration and NEEDS_REVISION

import uuid
from django.db import migrations, models


def generate_webhook_tokens(apps, schema_editor):
    TelegramBot = apps.get_model('core', 'TelegramBot')
    for bot in TelegramBot.objects.all():
        bot.webhook_secret_token = uuid.uuid4()
        bot.save(update_fields=['webhook_secret_token'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_scheduled_instagram_post'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfiguration',
            name='production_base_url',
            field=models.URLField(blank=True, help_text='Base URL of the site (e.g. https://iraniu.ir). Used to build webhook URL.', max_length=512),
        ),
        migrations.AddField(
            model_name='telegrambot',
            name='last_webhook_received',
            field=models.DateTimeField(blank=True, help_text='Last time Telegram sent an update to this bot (webhook health).', null=True),
        ),
        migrations.AddField(
            model_name='telegrambot',
            name='webhook_secret_token',
            field=models.UUIDField(db_index=True, editable=False, null=True, unique=True),
        ),
        migrations.RunPython(generate_webhook_tokens, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='telegrambot',
            name='webhook_secret_token',
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True),
        ),
        migrations.AlterField(
            model_name='telegrambot',
            name='mode',
            field=models.CharField(choices=[('webhook', 'Webhook'), ('polling', 'Polling')], default='webhook', help_text='Webhook: updates via HTTP. Polling: runbots worker fetches getUpdates.', max_length=16),
        ),
        migrations.AlterField(
            model_name='adrequest',
            name='status',
            field=models.CharField(choices=[('pending_ai', 'Pending AI'), ('pending_manual', 'Pending Manual'), ('needs_revision', 'Needs Revision'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('expired', 'Expired'), ('solved', 'Solved')], db_index=True, default='pending_ai', max_length=20),
        ),
    ]
