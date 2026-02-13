"""
Iraniu — Process Instagram queue: at most 5 posts per 24h with ±15 min jitter.
Run: python manage.py process_instagram_queue

Checks InstagramSettings.enable_instagram_queue; if ON, checks if at least 4.8h
has passed since last post, then publishes the oldest QUEUED ad (Feed + Story).
Logs to bot_log.txt. Safe to run from cron every 10 minutes or from runbots loop.
"""

from django.core.management.base import BaseCommand

from core.services.instagram_queue import run_queue_tick


class Command(BaseCommand):
    help = (
        'Process Instagram queue: if enabled and enough time has passed (4.8h ± 15 min), '
        'publish the oldest queued ad to Feed and Story.'
    )

    def handle(self, *args, **options):
        processed = run_queue_tick()
        if processed:
            self.stdout.write(self.style.SUCCESS('Instagram queue: one ad processed.'))
        else:
            self.stdout.write('Instagram queue: nothing to do (disabled, not time, or no queued ads).')
