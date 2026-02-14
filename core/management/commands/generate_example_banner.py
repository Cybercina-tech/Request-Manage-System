"""
Iraniu — Management command: generate an example ad banner for testing.
Run: python manage.py generate_example_banner [--format POST|STORY] [--output FILENAME]
     python manage.py generate_example_banner --portrait

Use this to verify ad banner generation and the monstrat.ttf phone font.
Portrait (1080x1350) is the default format for Telegram and Instagram Feed.
"""

from pathlib import Path

from django.core.management.base import BaseCommand
from django.conf import settings

from core.services.image_engine import generate_example_ad_banner, FORMAT_POST, FORMAT_STORY


class Command(BaseCommand):
    help = "Generate a sample ad banner for testing (phone font: monstrat.ttf). Portrait 1080x1350 default."

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=["POST", "STORY"],
            default="POST",
            help="Output format: POST (1080x1350 Portrait) or STORY (1080x1920)",
        )
        parser.add_argument(
            "--output",
            type=str,
            default=None,
            help="Output filename (default: example_ad_test.png, or test_portrait_banner.jpg with --portrait)",
        )
        parser.add_argument(
            "--portrait",
            action="store_true",
            help="Generate portrait sample and save to media/test_portrait_banner.jpg",
        )

    def handle(self, *args, **options):
        fmt = options["format"]
        output = options["output"]
        portrait = options["portrait"]

        output_path_arg = None
        if portrait:
            output = "test_portrait_banner.jpg"
            fmt = "POST"
            media_root = Path(settings.MEDIA_ROOT or "media")
            output_path_arg = media_root / output

        if output is None:
            output = "example_ad_test.png"

        self.stdout.write(f"Generating example ad banner ({fmt} 1080x1350)...")
        path = generate_example_ad_banner(
            format_type=fmt,
            output_filename=output,
            output_path=output_path_arg,
        )

        if not path:
            self.stderr.write(self.style.ERROR("Failed to generate banner."))
            return

        # Try to show a relative or readable path
        try:
            media_root = Path(settings.MEDIA_ROOT or "media")
            if Path(path).is_relative_to(media_root):
                rel = Path(path).relative_to(media_root)
                self.stdout.write(
                    self.style.SUCCESS(f"Banner saved: media/{rel}")
                )
            else:
                self.stdout.write(self.style.SUCCESS(f"Banner saved: {path}"))
        except (ValueError, TypeError):
            self.stdout.write(self.style.SUCCESS(f"Banner saved: {path}"))

        self.stdout.write("")
        self.stdout.write("Phone numbers use monstrat.ttf (place in static/fonts/ or media/ad_templates/fonts/).")
