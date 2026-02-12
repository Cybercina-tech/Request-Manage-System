"""
Iraniu — Management command: check Instagram token expiry.

Usage (cron, daily):
    python manage.py check_instagram_token

Checks if the long-lived access token is approaching expiry.
Fires a Warning notification at 14 days, Error at 7 days, and Critical at 0 days.
"""

import logging

from django.core.management.base import BaseCommand
from django.utils import timezone

logger = logging.getLogger('core.services.instagram')


class Command(BaseCommand):
    help = 'Check Instagram long-lived token expiry and send notifications.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--warn-days',
            type=int,
            default=14,
            help='Days before expiry to send a warning notification (default 14).',
        )
        parser.add_argument(
            '--critical-days',
            type=int,
            default=7,
            help='Days before expiry to send an error notification (default 7).',
        )

    def handle(self, *args, **options):
        from core.models import SiteConfiguration
        from core.notifications import send_notification

        warn_days = options['warn_days']
        critical_days = options['critical_days']

        config = SiteConfiguration.get_config()
        expires_at = config.instagram_token_expires_at

        if not expires_at:
            self.stdout.write(self.style.WARNING('No instagram_token_expires_at set. Skipping.'))
            return

        now = timezone.now()
        delta = expires_at - now
        days_left = delta.days

        if days_left < 0:
            msg = (
                f'Instagram token EXPIRED {abs(days_left)} day(s) ago. '
                'Instagram posting is non-functional. Please re-authenticate via Settings > Instagram > Connect Instagram.'
            )
            self.stdout.write(self.style.ERROR(msg))
            logger.error(msg)
            send_notification(
                level='error',
                message=msg,
                link='/settings/hub/instagram/',
                add_to_active_errors=True,
            )
        elif days_left <= critical_days:
            msg = (
                f'Instagram token expires in {days_left} day(s) ({expires_at.strftime("%Y-%m-%d %H:%M")}). '
                'Please refresh the token now via Settings > Instagram > Connect Instagram.'
            )
            self.stdout.write(self.style.ERROR(msg))
            logger.warning(msg)
            send_notification(
                level='error',
                message=msg,
                link='/settings/hub/instagram/',
                add_to_active_errors=True,
            )
        elif days_left <= warn_days:
            msg = (
                f'Instagram token expires in {days_left} day(s) ({expires_at.strftime("%Y-%m-%d %H:%M")}). '
                'Consider refreshing it soon via Settings > Instagram > Connect Instagram.'
            )
            self.stdout.write(self.style.WARNING(msg))
            logger.info(msg)
            send_notification(
                level='warning',
                message=msg,
                link='/settings/hub/instagram/',
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Instagram token OK — {days_left} day(s) remaining (expires {expires_at.strftime("%Y-%m-%d %H:%M")}).'
                )
            )
