"""
Iraniu — Management command: reset AdTemplate phone coordinates to code defaults.
Run: python manage.py reset_template_phone_coords [--template-id ID]

Use this when template coordinates in DB override our phone defaults (size 48, etc.).
"""

from django.core.management.base import BaseCommand

from core.models import AdTemplate, default_adtemplate_coordinates


class Command(BaseCommand):
    help = "Reset phone layer coordinates to code defaults (size 48, letter_spacing 2, etc.)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--template-id",
            type=int,
            help="Template ID to update (default: all active templates)",
        )

    def handle(self, *args, **options):
        tid = options.get("template_id")
        defaults = default_adtemplate_coordinates()
        phone_defaults = defaults.get("phone", {})

        if tid:
            qs = AdTemplate.objects.filter(pk=tid)
            if not qs.exists():
                self.stderr.write(self.style.ERROR(f"Template ID {tid} not found."))
                return
        else:
            qs = AdTemplate.objects.filter(is_active=True)

        updated = 0
        for tpl in qs:
            coords = dict(tpl.coordinates) if tpl.coordinates else {}
            if "phone" not in coords:
                coords["phone"] = {}
            coords["phone"].update(phone_defaults)
            tpl.coordinates = coords
            tpl.save(update_fields=["coordinates"])
            updated += 1
            self.stdout.write(f"Updated template {tpl.pk} ({tpl.name})")

        self.stdout.write(self.style.SUCCESS(f"Updated {updated} template(s)."))
