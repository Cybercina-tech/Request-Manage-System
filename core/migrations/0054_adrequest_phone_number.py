# Generated manually for AdRequest.phone_number + backfill from contact_snapshot

from django.db import migrations, models


def backfill_phone_from_snapshot(apps, schema_editor):
    AdRequest = apps.get_model("core", "AdRequest")
    for ad in AdRequest.objects.all().only("id", "contact_snapshot", "phone_number"):
        if ad.phone_number:
            continue
        snap = ad.contact_snapshot or {}
        if not isinstance(snap, dict):
            continue
        phone = (snap.get("phone") or "").strip()[:20]
        if phone:
            ad.phone_number = phone
            ad.save(update_fields=["phone_number"])


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0053_add_system_log"),
    ]

    operations = [
        migrations.AddField(
            model_name="adrequest",
            name="phone_number",
            field=models.CharField(
                blank=True,
                help_text="Phone at submission time, stored as raw string (e.g. +98… or leading zeros).",
                max_length=20,
            ),
        ),
        migrations.RunPython(backfill_phone_from_snapshot, noop_reverse),
    ]
