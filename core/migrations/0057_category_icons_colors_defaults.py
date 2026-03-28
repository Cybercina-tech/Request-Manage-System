# Seed Lucide icon names and distinct hex colors per category slug; default icon for new rows.

from django.db import migrations, models


def seed_category_icons_and_colors(apps, schema_editor):
    Category = apps.get_model("core", "Category")
    # Lucide icon names + distinct palette (not all default purple)
    by_slug = {
        "job_vacancy": ("briefcase", "#00E676"),
        "rent": ("home", "#26C6DA"),
        "events": ("calendar-days", "#FF9800"),
        "services": ("sparkles", "#8B5CF6"),
        "sale": ("shopping-bag", "#F59E0B"),
        "other": ("layout-grid", "#64748B"),
    }
    for cat in Category.objects.all():
        slug = (cat.slug or "").strip()
        if slug in by_slug:
            icon, color = by_slug[slug]
            Category.objects.filter(pk=cat.pk).update(icon=icon, color=color)
        elif not (getattr(cat, "icon", None) or "").strip():
            Category.objects.filter(pk=cat.pk).update(icon="folder")


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0056_adrequest_content_validators"),
    ]

    operations = [
        migrations.RunPython(seed_category_icons_and_colors, noop_reverse),
        migrations.AlterField(
            model_name="category",
            name="icon",
            field=models.CharField(
                blank=True,
                default="circle",
                help_text="Lucide icon name (e.g. home, briefcase). Default: circle.",
                max_length=64,
            ),
        ),
    ]
