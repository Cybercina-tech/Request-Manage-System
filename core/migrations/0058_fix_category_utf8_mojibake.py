"""Placeholder: previous data-repair step removed; keeps migration history linear."""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0057_category_icons_colors_defaults"),
    ]

    operations = [
        migrations.RunPython(migrations.RunPython.noop, migrations.RunPython.noop),
    ]
