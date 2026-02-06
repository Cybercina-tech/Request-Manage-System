# Generated manually for Iraniu

import uuid
from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='SiteConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_ai_enabled', models.BooleanField(default=False)),
                ('openai_api_key', models.CharField(blank=True, max_length=255)),
                ('openai_model', models.CharField(default='gpt-3.5-turbo', help_text='e.g. gpt-4o, gpt-3.5-turbo', max_length=64)),
                ('ai_system_prompt', models.TextField(blank=True, default='You are a moderator for Iraniu. Check if this ad follows community rules. Reply with JSON: {"approved": true/false, "reason": "optional reason"}')),
                ('telegram_bot_token', models.CharField(blank=True, max_length=255)),
                ('telegram_webhook_url', models.URLField(blank=True)),
                ('use_webhook', models.BooleanField(default=False)),
                ('approval_message_template', models.TextField(default='Your ad has been approved. Ad ID: {ad_id}. Thank you for using Iraniu.')),
                ('rejection_message_template', models.TextField(default='Your ad was not approved. Reason: {reason}. Ad ID: {ad_id}.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Site Configuration',
                'verbose_name_plural': 'Site Configuration',
            },
        ),
        migrations.CreateModel(
            name='AdRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('uuid', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('category', models.CharField(choices=[('job_vacancy', 'Job'), ('rent', 'Rent'), ('events', 'Events'), ('services', 'Services'), ('sale', 'Sale'), ('other', 'Other')], default='other', max_length=32)),
                ('status', models.CharField(choices=[('pending_ai', 'Pending AI'), ('pending_manual', 'Pending Manual'), ('approved', 'Approved'), ('rejected', 'Rejected'), ('expired', 'Expired'), ('solved', 'Solved')], db_index=True, default='pending_ai', max_length=20)),
                ('content', models.TextField()),
                ('rejection_reason', models.TextField(blank=True)),
                ('ai_suggested_reason', models.TextField(blank=True)),
                ('telegram_user_id', models.BigIntegerField(blank=True, null=True)),
                ('telegram_username', models.CharField(blank=True, max_length=128)),
                ('raw_telegram_json', models.JSONField(blank=True, null=True)),
                ('created_at', models.DateTimeField(db_index=True, default=timezone.now)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='adrequest',
            index=models.Index(fields=['status'], name='core_adrequ_status_8a0c0d_idx'),
        ),
        migrations.AddIndex(
            model_name='adrequest',
            index=models.Index(fields=['created_at'], name='core_adrequ_created_9b0e1f_idx'),
        ),
        migrations.AddIndex(
            model_name='adrequest',
            index=models.Index(fields=['category', 'status'], name='core_adrequ_categor_7d2a3b_idx'),
        ),
    ]
