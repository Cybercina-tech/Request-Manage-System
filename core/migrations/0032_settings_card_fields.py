# Generated manually for modular CRM settings (card-based architecture)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_alter_siteconfiguration_options_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfiguration',
            name='instagram_app_id',
            field=models.CharField(blank=True, help_text='Facebook App ID (Meta for Developers).', max_length=64),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='instagram_app_secret_encrypted',
            field=models.TextField(blank=True, help_text='Facebook App Secret (encrypted at rest).'),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='default_watermark',
            field=models.ImageField(blank=True, help_text='Default watermark image for generated ads.', null=True, upload_to='settings/watermarks/'),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='default_primary_color',
            field=models.CharField(blank=True, default='#2b8adf', help_text='Default primary color (hex) for new ads.', max_length=18),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='default_secondary_color',
            field=models.CharField(blank=True, default='#3fb98f', help_text='Default secondary color (hex) for new ads.', max_length=18),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='default_accent_color',
            field=models.CharField(blank=True, default='#39a0f1', help_text='Default accent color (hex) for new ads.', max_length=18),
        ),
    ]
