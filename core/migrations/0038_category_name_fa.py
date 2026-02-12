"""Add name_fa (Persian name) to Category for image generation."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0037_deliverylog_channel_choices'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='name_fa',
            field=models.CharField(
                blank=True,
                default='',
                help_text='Persian name for image generation (e.g. فروش ویژه). Falls back to name if empty.',
                max_length=100,
            ),
        ),
    ]
