"""
Iraniu — Rotate system logs: delete entries older than 30 days.
Run: python manage.py rotate_system_logs

Keeps the SQLite database light. Schedule via cron:
  0 3 * * * cd /path/to/project && python manage.py rotate_system_logs
"""

from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Delete SystemLog entries older than 30 days to keep the database light.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=30,
            help='Delete logs older than this many days (default: 30)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Only report what would be deleted, do not delete',
        )

    def handle(self, *args, **options):
        from core.models import SystemLog

        days = max(1, options['days'])
        dry_run = options['dry_run']
        cutoff = timezone.now() - timezone.timedelta(days=days)

        qs = SystemLog.objects.filter(created_at__lt=cutoff)
        count = qs.count()

        if count == 0:
            self.stdout.write(f'No system logs older than {days} days.')
            return

        if dry_run:
            self.stdout.write(self.style.WARNING(f'Would delete {count} log(s) older than {days} days.'))
            return

        qs.delete()
        self.stdout.write(self.style.SUCCESS(f'Deleted {count} system log(s) older than {days} days.'))
