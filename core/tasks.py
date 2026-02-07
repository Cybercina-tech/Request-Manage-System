"""
Iraniu — Celery tasks. Optional: add celery, django-celery-beat to requirements.
Schedule: celery beat runs publish_scheduled_instagram_posts every minute.
"""

import logging

logger = logging.getLogger(__name__)

try:
    from celery import shared_task
except ImportError:
    shared_task = lambda *a, **kw: lambda f: f


@shared_task(bind=True, max_retries=3)
def publish_scheduled_instagram_posts_task(self):
    """Publish pending scheduled Instagram posts. Call via Celery Beat."""
    from django.core.management import call_command
    call_command('publish_scheduled_instagram_posts')


@shared_task(bind=True, max_retries=3)
def post_to_instagram_task(self, image_url: str, caption: str, config_id: int | None = None):
    """Post custom content to Instagram. Returns dict with success, message, id."""
    from core.models import InstagramConfiguration
    from core.services.instagram import InstagramService

    config = InstagramConfiguration.objects.filter(pk=config_id, is_active=True).first() if config_id else None
    return InstagramService.post_custom(image_url=image_url, caption=caption, config=config)
