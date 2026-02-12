"""
Seed the 'Main Template' with exact coordinates for Category, Description, and Phone layers.

Background: uses the default fallback (static/images/default_template/Template.png).
Font: uses the default fallback (static/fonts/Persian.ttf).
Coordinates match the production layout designed for the Iraniu template.
"""

from django.db import migrations


MAIN_TEMPLATE_COORDINATES = {
    "category": {
        "x": 180,
        "y": 290,
        "size": 90,
        "max_width": 700,
        "color": "#EEFF00",
        "font_path": "",
        "align": "center",
        "bold": True,
    },
    "description": {
        "x": 215,
        "y": 600,
        "size": 55,
        "max_width": 650,
        "color": "#FFFFFF",
        "font_path": "",
        "align": "center",
        "bold": True,
    },
    "phone": {
        "x": 250,
        "y": 1150,
        "size": 50,
        "max_width": 550,
        "color": "#131111",
        "font_path": "",
        "align": "center",
        "bold": True,
    },
}

# Story coordinates: same as post but with Y shifted +285px
STORY_Y_OFFSET = 285
MAIN_TEMPLATE_STORY_COORDINATES = {}
for _key, _conf in MAIN_TEMPLATE_COORDINATES.items():
    _new = dict(_conf)
    _new["y"] = _conf["y"] + STORY_Y_OFFSET
    MAIN_TEMPLATE_STORY_COORDINATES[_key] = _new

# Known categories with Persian names
CATEGORY_PERSIAN_NAMES = {
    "real-estate": "املاک",
    "real_estate": "املاک",
    "job": "استخدام",
    "jobs": "استخدام",
    "vehicle": "خودرو",
    "vehicles": "خودرو",
    "car": "خودرو",
    "service": "خدمات",
    "services": "خدمات",
    "electronics": "الکترونیک",
    "digital": "دیجیتال",
    "other": "سایر",
    "education": "آموزش",
    "health": "سلامت",
    "beauty": "زیبایی",
    "food": "غذا",
    "travel": "سفر",
    "sport": "ورزش",
    "sports": "ورزش",
    "clothing": "پوشاک",
    "furniture": "مبلمان",
    "pet": "حیوانات",
    "pets": "حیوانات",
}


def seed_main_template(apps, schema_editor):
    """Create the Main Template if it doesn't already exist."""
    AdTemplate = apps.get_model("core", "AdTemplate")

    # Don't duplicate if already exists
    if AdTemplate.objects.filter(name="Main Template").exists():
        return

    AdTemplate.objects.create(
        name="Main Template",
        # background_image and font_file are left empty intentionally:
        # The image engine falls back to static/images/default_template/Template.png
        # and static/fonts/Persian.ttf automatically.
        coordinates=MAIN_TEMPLATE_COORDINATES,
        story_coordinates=MAIN_TEMPLATE_STORY_COORDINATES,
        is_active=True,
    )


def seed_category_persian_names(apps, schema_editor):
    """Populate name_fa for known categories based on slug."""
    Category = apps.get_model("core", "Category")

    for cat in Category.objects.all():
        if cat.name_fa:
            continue  # already has a Persian name
        slug_lower = (cat.slug or "").lower().strip()
        name_lower = (cat.name or "").lower().strip()
        fa_name = CATEGORY_PERSIAN_NAMES.get(slug_lower) or CATEGORY_PERSIAN_NAMES.get(name_lower)
        if fa_name:
            cat.name_fa = fa_name
            cat.save(update_fields=["name_fa"])


def reverse_seed(apps, schema_editor):
    """Remove the seeded Main Template on reverse migration."""
    AdTemplate = apps.get_model("core", "AdTemplate")
    AdTemplate.objects.filter(name="Main Template").delete()


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0038_category_name_fa"),
    ]

    operations = [
        migrations.RunPython(seed_main_template, reverse_seed),
        migrations.RunPython(seed_category_persian_names, migrations.RunPython.noop),
    ]
