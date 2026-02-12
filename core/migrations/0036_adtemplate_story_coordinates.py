"""Add story_coordinates JSONField to AdTemplate for Instagram Story (1080x1920) layout."""

from django.db import migrations, models
import core.models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0035_instagram_oauth_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='adtemplate',
            name='story_coordinates',
            field=models.JSONField(
                blank=True,
                default=core.models.default_story_coordinates,
                help_text=(
                    'Coordinates for Story format (1080x1920). Same structure as coordinates. '
                    'If empty, auto-generated from post coordinates using safety zone logic.'
                ),
            ),
        ),
    ]
