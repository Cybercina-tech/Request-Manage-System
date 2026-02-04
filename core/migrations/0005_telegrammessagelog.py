# Generated for Iranio — Message history

from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_telegrambot_telegramsession_adrequest_bot'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramMessageLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('telegram_user_id', models.BigIntegerField(db_index=True)),
                ('direction', models.CharField(choices=[('in', 'In'), ('out', 'Out')], max_length=8)),
                ('text', models.TextField(blank=True)),
                ('raw_payload', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(db_index=True, default=timezone.now)),
                ('bot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='message_logs', to='core.telegrambot')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='telegrammessagelog',
            index=models.Index(fields=['bot', 'telegram_user_id', 'created_at'], name='core_telegr_bot_id_9babbe_idx'),
        ),
    ]
