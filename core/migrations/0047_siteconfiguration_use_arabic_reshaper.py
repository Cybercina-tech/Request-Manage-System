# Generated manually for adaptive rendering toggle

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0046_default_iraniu_bot'),
    ]

    operations = [
        migrations.AddField(
            model_name='siteconfiguration',
            name='use_arabic_reshaper',
            field=models.BooleanField(
                default=True,
                help_text='When ON, use arabic_reshaper+bidi for Persian text in images and templates. Turn OFF if text looks garbled (modern fonts/browsers often render RTL correctly without it).',
            ),
        ),
    ]
