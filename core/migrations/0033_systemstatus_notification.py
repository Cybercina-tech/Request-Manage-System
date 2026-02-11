# Generated manually for System Watchdog & Notification Center

import django.utils.timezone
from django.db import migrations, models


def default_active_errors():
    return []


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_settings_card_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='SystemStatus',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('last_heartbeat', models.DateTimeField(blank=True, db_index=True, help_text='Last heartbeat from runbots worker; if older than 2 min, worker is OFFLINE.', null=True)),
                ('is_bot_active', models.BooleanField(default=False, help_text='True when runbots process is running and sending heartbeats.')),
                ('active_errors', models.JSONField(blank=True, default=default_active_errors, help_text='List of current error messages, e.g. ["Instagram Token Expired"].')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'System Status',
                'verbose_name_plural': 'System Status',
            },
        ),
        migrations.CreateModel(
            name='Notification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('level', models.CharField(choices=[('success', 'Success'), ('info', 'Info'), ('warning', 'Warning'), ('error', 'Error')], db_index=True, max_length=16)),
                ('message', models.TextField(help_text='Notification text (supports Persian/RTL).')),
                ('link', models.URLField(blank=True, help_text='Optional URL to fix the issue.')),
                ('is_read', models.BooleanField(db_index=True, default=False)),
                ('created_at', models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
            options={
                'verbose_name': 'Notification',
                'verbose_name_plural': 'Notifications',
                'ordering': ['-created_at'],
            },
        ),
    ]
