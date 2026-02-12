"""Add instagram_token_expires_at and instagram_oauth_state to SiteConfiguration."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0034_alter_adtemplate_coordinates_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfiguration',
            name='instagram_token_expires_at',
            field=models.DateTimeField(
                null=True,
                blank=True,
                help_text='When the current long-lived access token expires (auto-set on OAuth flow).',
            ),
        ),
        migrations.AddField(
            model_name='siteconfiguration',
            name='instagram_oauth_state',
            field=models.CharField(
                max_length=128,
                blank=True,
                help_text='CSRF state token for in-progress Instagram OAuth flow.',
            ),
        ),
    ]
