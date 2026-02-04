# Generated for Iranio — User profile, OTP, AdRequest user + contact_snapshot

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_telegrammessagelog'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramUser',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('telegram_user_id', models.BigIntegerField(db_index=True, unique=True)),
                ('username', models.CharField(blank=True, max_length=128, null=True)),
                ('first_name', models.CharField(blank=True, max_length=128, null=True)),
                ('last_name', models.CharField(blank=True, max_length=128, null=True)),
                ('language_code', models.CharField(blank=True, max_length=8, null=True)),
                ('is_bot', models.BooleanField(default=False)),
                ('phone_number', models.CharField(blank=True, max_length=20, null=True)),
                ('email', models.EmailField(blank=True, max_length=254, null=True)),
                ('phone_verified', models.BooleanField(default=False)),
                ('email_verified', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('last_seen', models.DateTimeField(blank=True, db_index=True, null=True)),
            ],
            options={
                'ordering': ['-last_seen', '-created_at'],
                'verbose_name': 'Telegram User',
                'verbose_name_plural': 'Telegram Users',
            },
        ),
        migrations.CreateModel(
            name='VerificationCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('channel', models.CharField(choices=[('email', 'Email'), ('phone', 'Phone')], max_length=16)),
                ('code_hashed', models.CharField(max_length=128)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('used', models.BooleanField(db_index=True, default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='verification_codes', to='core.telegramuser')),
            ],
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Verification Code',
                'verbose_name_plural': 'Verification Codes',
            },
        ),
        migrations.AddField(
            model_name='adrequest',
            name='user',
            field=models.ForeignKey(blank=True, help_text='Telegram user profile (if via Telegram)', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='ad_requests', to='core.telegramuser'),
        ),
        migrations.AddField(
            model_name='adrequest',
            name='contact_snapshot',
            field=models.JSONField(blank=True, default=dict, help_text='Contact at submission time: phone, email, verified_phone, verified_email'),
        ),
    ]
