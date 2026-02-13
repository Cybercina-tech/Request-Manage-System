"""
Iraniu — Instagram queue processor: 5 posts per 24h with jitter.
Used by manage.py process_instagram_queue and by runbots supervisor loop.
"""

import logging
import random
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from core.models import AdRequest, DeliveryLog, InstagramSettings
from core.services.delivery import DeliveryService

logger = logging.getLogger(__name__)
_log_bot = logging.getLogger('core.instagram.bot')

# 24h / 5 posts ≈ 4.8 hours between posts
HOURS_BETWEEN_POSTS = 24.0 / 5.0
JITTER_MINUTES = 15


def _log_queue(message: str, ad_uuid=None, detail: str = ''):
    """Write one line to bot_log.txt for queue actions."""
    parts = [message]
    if ad_uuid:
        parts.append(f'ad={ad_uuid}')
    if detail:
        parts.append(detail)
    _log_bot.info(' '.join(parts))


def get_next_post_allowed_at(ig_settings: InstagramSettings):
    """
    Return the earliest time we're allowed to post (last_post_time + 4.8h ± 15 min).
    If last_post_time is None, we're allowed now.
    """
    last = ig_settings.last_post_time
    if not last:
        return timezone.now()
    base_interval = timedelta(hours=HOURS_BETWEEN_POSTS)
    jitter = timedelta(minutes=random.uniform(-JITTER_MINUTES, JITTER_MINUTES))
    return last + base_interval + jitter


def process_one_queued_ad() -> bool:
    """
    If it's time to post and there is a queued ad, pick the oldest, send Feed + Story, update state.
    Returns True if an ad was processed (success or failure); False if skipped (no queue / not time).
    Uses transaction.atomic and select_for_update on InstagramSettings for concurrency safety.
    """
    now = timezone.now()
    with transaction.atomic():
        try:
            ig_settings = InstagramSettings.objects.select_for_update().get(pk=1)
        except InstagramSettings.DoesNotExist:
            InstagramSettings.get_settings()
            return False
        if not getattr(ig_settings, 'enable_instagram_queue', False):
            return False

        next_allowed = get_next_post_allowed_at(ig_settings)
        if now < next_allowed:
            return False

        # Oldest queued ad (by created_at)
        ad = (
            AdRequest.objects.filter(
                status=AdRequest.Status.APPROVED,
                instagram_queue_status='queued',
            )
            .order_by('created_at')
            .first()
        )
        if not ad:
            return False

        # Lock the ad so two workers don't process the same one
        ad = AdRequest.objects.select_for_update().get(pk=ad.pk)
        if ad.instagram_queue_status != 'queued':
            return False

        # Send both channels (force_deliver=True to skip queue check); failures don't block next ads
        feed_ok = DeliveryService.send(ad, 'instagram', force_deliver=True)
        story_ok = DeliveryService.send(ad, 'instagram_story', force_deliver=True)

        if feed_ok or story_ok:
            ig_settings.last_post_time = now
            ig_settings.save(update_fields=['last_post_time', 'updated_at'])
            ad.instagram_queue_status = 'sent'
            ad.save(update_fields=['instagram_queue_status'])
            _log_queue(
                'Instagram queue SUCCESS',
                ad.uuid,
                f'feed={feed_ok} story={story_ok}',
            )
        else:
            ad.instagram_queue_status = 'failed'
            ad.save(update_fields=['instagram_queue_status'])
            _log_queue('Instagram queue FAILED', ad.uuid, 'Feed and Story delivery failed')

    return True


def run_queue_tick() -> bool:
    """
    Single tick: check time and maybe process one queued ad.
    Safe to call from runbots loop every 10 minutes.
    Returns True if an ad was processed.
    """
    try:
        return process_one_queued_ad()
    except InstagramSettings.DoesNotExist:
        return False
    except Exception as e:
        logger.exception("Instagram queue tick failed: %s", e)
        _log_bot.warning("Instagram queue tick error: %s", str(e)[:200])
        return False
