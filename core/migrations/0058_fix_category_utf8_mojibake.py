"""Fix Category.name / name_fa stored as UTF-8 misread as Latin-1 (mojibake)."""

from django.db import migrations


def fix_category_mojibake(apps, schema_editor):
    from core.utils.encoding import repair_utf8_misread_as_latin1

    Category = apps.get_model("core", "Category")
    for cat in Category.objects.all():
        new_name = repair_utf8_misread_as_latin1(cat.name or "")
        new_fa = repair_utf8_misread_as_latin1(cat.name_fa or "")
        if new_name != cat.name or new_fa != cat.name_fa:
            Category.objects.filter(pk=cat.pk).update(name=new_name, name_fa=new_fa)


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0057_category_icons_colors_defaults"),
    ]

    operations = [
        migrations.RunPython(fix_category_mojibake, migrations.RunPython.noop),
    ]
