"""
Iraniu — Management command: save Instagram access token and User ID to SiteConfiguration.

Usage:
  # From environment (set INSTAGRAM_ACCESS_TOKEN and optionally INSTAGRAM_USER_ID in .env)
  python manage.py set_instagram_token

  # From arguments (token not stored in shell history if using env is preferred)
  python manage.py set_instagram_token --token=IGAAT... --ig-user-id=17841478639951731

The token is stored encrypted in SiteConfiguration.facebook_access_token_encrypted.
The Instagram User ID is stored in SiteConfiguration.instagram_business_id.
"""

import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Save Instagram access token and Instagram User ID to SiteConfiguration (encrypted).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--token',
            type=str,
            default='',
            help='Long-lived Instagram/Facebook access token. If omitted, uses INSTAGRAM_ACCESS_TOKEN from environment.',
        )
        parser.add_argument(
            '--ig-user-id',
            type=str,
            default='',
            help='Instagram Graph API User ID (e.g. 17841478639951731). If omitted, uses INSTAGRAM_USER_ID from env or default.',
        )

    def handle(self, *args, **options):
        from core.models import SiteConfiguration

        token = (options.get('token') or '').strip() or (os.environ.get('INSTAGRAM_ACCESS_TOKEN') or '').strip()
        ig_user_id = (
            (options.get('ig_user_id') or '').strip()
            or (os.environ.get('INSTAGRAM_USER_ID') or '').strip()
            or '17841478639951731'
        )

        if not token:
            self.stdout.write(
                self.style.ERROR(
                    'No token provided. Set INSTAGRAM_ACCESS_TOKEN in .env or pass --token=...'
                )
            )
            return

        config = SiteConfiguration.get_config()
        config.set_facebook_access_token(token)
        config.instagram_business_id = ig_user_id
        config.save(update_fields=['facebook_access_token_encrypted', 'instagram_business_id'])

        self.stdout.write(
            self.style.SUCCESS(
                f'Instagram token and User ID saved. instagram_business_id={ig_user_id}.'
            )
        )
