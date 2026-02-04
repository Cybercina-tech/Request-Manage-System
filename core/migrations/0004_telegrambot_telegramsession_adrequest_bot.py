# Generated for Iranio — Telegram Bot Management

from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_rename_core_adrequ_status_8a0c0d_idx_core_adrequ_status_9babbe_idx_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramBot',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(help_text='Human-readable name', max_length=128)),
                ('bot_token_encrypted', models.TextField(blank=True)),
                ('username', models.CharField(blank=True, help_text='Bot username without @', max_length=64)),
                ('is_active', models.BooleanField(default=True)),
                ('webhook_url', models.URLField(blank=True)),
                ('webhook_secret', models.CharField(blank=True, help_text='Secret for webhook verification', max_length=64)),
                ('last_heartbeat', models.DateTimeField(blank=True, null=True)),
                ('status', models.CharField(choices=[('online', 'Online'), ('offline', 'Offline'), ('error', 'Error')], db_index=True, default='offline', max_length=16)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['name'],
                'verbose_name': 'Telegram Bot',
                'verbose_name_plural': 'Telegram Bots',
            },
        ),
        migrations.CreateModel(
            name='TelegramSession',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('telegram_user_id', models.BigIntegerField(db_index=True)),
                ('language', models.CharField(blank=True, max_length=8, null=True)),
                ('state', models.CharField(choices=[('START', 'Start'), ('SELECT_LANGUAGE', 'Select Language'), ('MAIN_MENU', 'Main Menu'), ('ENTER_CONTENT', 'Enter Content'), ('SELECT_CATEGORY', 'Select Category'), ('CONFIRM', 'Confirm'), ('SUBMITTED', 'Submitted'), ('EDITING', 'Editing')], db_index=True, default='START', max_length=32)),
                ('context', models.JSONField(blank=True, default=dict)),
                ('last_activity', models.DateTimeField(db_index=True, default=timezone.now)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('bot', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sessions', to='core.telegrambot')),
            ],
            options={
                'ordering': ['-last_activity'],
                'verbose_name': 'Telegram Session',
                'verbose_name_plural': 'Telegram Sessions',
                'unique_together': {('telegram_user_id', 'bot')},
            },
        ),
        migrations.AddField(
            model_name='adrequest',
            name='bot',
            field=models.ForeignKey(blank=True, help_text='Bot through which ad was submitted (if via Telegram)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ad_requests', to='core.telegrambot'),
        ),
        migrations.AddIndex(
            model_name='telegramsession',
            index=models.Index(fields=['telegram_user_id', 'bot'], name='core_telegr_telegra_9babbe_idx'),
        ),
        migrations.AddIndex(
            model_name='telegramsession',
            index=models.Index(fields=['state'], name='core_telegr_state_9babbe_idx'),
        ),
    ]
