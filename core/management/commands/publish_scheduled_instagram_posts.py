"""
Iraniu — Publish scheduled Instagram posts whose scheduled_at has passed.
Run via cron every minute: * * * * * python manage.py publish_scheduled_instagram_posts
"""

import logging
from django.core.management.base import BaseCommand
from django.utils import timezone

from core.models import ScheduledInstagramPost
from core.services.instagram import InstagramService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Publish pending scheduled Instagram posts whose scheduled_at has passed'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='List posts that would be published without posting',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        now = timezone.now()
        qs = ScheduledInstagramPost.objects.filter(
            status=ScheduledInstagramPost.Status.PENDING,
            scheduled_at__lte=now,
        ).order_by('scheduled_at')[:50]

        count = qs.count()
        if count == 0:
            if not dry_run:
                self.stdout.write('No pending scheduled posts to publish.')
            return

        if dry_run:
            self.stdout.write(f'Would publish {count} post(s):')
            for p in qs:
                self.stdout.write(f'  - pk={p.pk} scheduled_at={p.scheduled_at}')
            return

        for post in qs:
            result = InstagramService.post_custom(
                image_url=post.image_url,
                caption=post.caption,
            )
            if result.get('success'):
                post.status = ScheduledInstagramPost.Status.PUBLISHED
                post.instagram_media_id = str(result.get('id', ''))
                post.published_at = now
                post.error_message = ''
                post.save()
                logger.info('Published scheduled post pk=%s media_id=%s', post.pk, post.instagram_media_id)
                self.stdout.write(self.style.SUCCESS(f'Published pk={post.pk}'))
            else:
                post.status = ScheduledInstagramPost.Status.FAILED
                post.error_message = result.get('message', 'Unknown error')[:500]
                post.save()
                logger.warning('Failed scheduled post pk=%s: %s', post.pk, post.error_message)
                self.stdout.write(self.style.ERROR(f'Failed pk={post.pk}: {post.error_message}'))
