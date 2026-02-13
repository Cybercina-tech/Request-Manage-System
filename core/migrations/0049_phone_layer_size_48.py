# Generated manually — set phone layer size to 48 for all AdTemplates

from django.db import migrations


def set_phone_size_48(apps, schema_editor):
    """Update all AdTemplate coordinates to use phone size 48."""
    AdTemplate = apps.get_model("core", "AdTemplate")
    for tpl in AdTemplate.objects.all():
        updated = False
        coords = tpl.coordinates or {}
        if isinstance(coords.get("phone"), dict):
            coords["phone"]["size"] = 48
            coords["phone"]["max_width"] = 450
            coords["phone"]["letter_spacing"] = 2
            coords["phone"]["x"] = 300
            coords["phone"]["y"] = 1150
            tpl.coordinates = coords
            updated = True
        if tpl.story_coordinates and isinstance(tpl.story_coordinates.get("phone"), dict):
            sc = dict(tpl.story_coordinates)
            sc["phone"] = dict(sc["phone"])
            sc["phone"]["size"] = 48
            sc["phone"]["max_width"] = 450
            sc["phone"]["letter_spacing"] = 2
            sc["phone"]["x"] = 300
            sc["phone"]["y"] = 1150 + 285
            tpl.story_coordinates = sc
            updated = True
        if updated:
            tpl.save(update_fields=["coordinates", "story_coordinates"])


def reverse_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0048_adrequest_generated_images_instagram_ids"),
    ]

    operations = [
        migrations.RunPython(set_phone_size_48, reverse_noop),
    ]
