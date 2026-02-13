"""
Iraniu — Instagram Graph API for Business accounts.
Post images to Feed or Story. Uses container creation + publish flow.
Media URLs must be absolute and publicly accessible (no login); /media/ is in PUBLIC_PATHS.
"""

import logging
import os
import time
from pathlib import Path

import requests

from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_BASE = 'https://graph.facebook.com/v18.0'
REQUEST_TIMEOUT = 15
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 0.5

# Production base URL for Instagram image_url. Instagram requires HTTPS and public 200 OK.
DEFAULT_INSTAGRAM_BASE_URL = 'https://request.iraniu.uk'


def get_instagram_base_url() -> str:
    """
    Return the absolute base URL (no trailing slash) for building media URLs.
    Order: settings.INSTAGRAM_BASE_URL, env INSTAGRAM_BASE_URL, SiteConfiguration.production_base_url,
    then DEFAULT_INSTAGRAM_BASE_URL.
    """
    base = (
        (getattr(settings, 'INSTAGRAM_BASE_URL', '') or '').strip()
        or os.environ.get('INSTAGRAM_BASE_URL', '').strip()
    )
    if base:
        return base.rstrip('/')
    from core.models import SiteConfiguration
    site = SiteConfiguration.get_config()
    base = (site.production_base_url or '').strip().rstrip('/')
    if base:
        return base
    return DEFAULT_INSTAGRAM_BASE_URL


def get_absolute_media_url(file_field) -> str | None:
    """
    Build a public absolute URL for a FileField/ImageField so Instagram can fetch the image.
    Requires no login (middleware must allow /media/ as public).

    Args:
        file_field: Django ImageField/FileField value (e.g. ad.generated_image, ad.generated_story_image).
                    Can be None or falsy.

    Returns:
        Full URL e.g. https://request.iraniu.uk/media/generated_ads/xxx.png, or None if no file.
    """
    if not file_field:
        return None
    try:
        url = getattr(file_field, 'url', None) or ''
    except (ValueError, OSError):
        return None
    url = (url or '').strip()
    if not url:
        return None
    base = get_instagram_base_url()
    if url.startswith('http'):
        return url
    if not url.startswith('/'):
        url = '/' + url
    return f'{base}{url}'


def _get_credentials():
    """
    Get Instagram credentials from SiteConfiguration or InstagramConfiguration.
    Returns (ig_user_id, access_token) or (None, None).
    """
    from core.models import InstagramConfiguration, SiteConfiguration

    config = InstagramConfiguration.objects.filter(is_active=True).first()
    if config:
        token = config.get_decrypted_token()
        ig_id = (config.ig_user_id or '').strip()
        if ig_id and token:
            return ig_id, token
        if token:
            try:
                r = requests.get(
                    f'{GRAPH_API_BASE}/me',
                    params={'access_token': token, 'fields': 'id'},
                    timeout=REQUEST_TIMEOUT,
                )
                if r.ok:
                    ig_id = r.json().get('id', '')
                    if ig_id:
                        config.ig_user_id = ig_id
                        config.save(update_fields=['ig_user_id'])
                        return ig_id, token
            except Exception as e:
                logger.warning("Could not resolve ig_user_id: %s", e)

    site = SiteConfiguration.get_config()
    ig_id = (getattr(site, 'instagram_business_id', None) or '').strip()
    token = (site.get_facebook_access_token() if hasattr(site, 'get_facebook_access_token') else '') or ''
    if ig_id and token:
        return ig_id, token
    return None, None


def _path_to_public_url(image_path: str) -> str | None:
    """
    Convert local filesystem path (under MEDIA_ROOT) to public absolute URL for Instagram.
    Uses get_instagram_base_url() so image_url is always absolute (e.g. https://request.iraniu.uk/...).
    """
    path = Path(image_path)
    if not path.exists():
        return None
    media_root = Path(getattr(settings, 'MEDIA_ROOT', '') or '')
    if not media_root:
        media_root = Path(settings.BASE_DIR) / 'media'
    try:
        rel = path.resolve().relative_to(media_root.resolve())
    except ValueError:
        return None
    rel_str = str(rel).replace('\\', '/')
    base = get_instagram_base_url()
    media_url = getattr(settings, 'MEDIA_URL', '/media/').rstrip('/')
    if not media_url.startswith('/'):
        media_url = '/' + media_url
    return f'{base}{media_url}/{rel_str}'


def post_to_instagram(
    image_path: str,
    caption: str = '',
    is_story: bool = False,
) -> dict:
    """
    Post image to Instagram via Graph API.
    Uses public absolute URL (e.g. https://request.iraniu.uk/media/...) so Meta can fetch the image.
    Feed posts must use generated_image; Stories must use generated_story_image (caller responsibility).

    - Feed: square/portrait image with caption (image_path → generated_image)
    - Story: 9:16 image, no caption (image_path → generated_story_image)

    Args:
        image_path: Absolute filesystem path to image (PNG/JPEG)
        caption: Caption text (Feed only; max 2200 chars)
        is_story: If True, post as Story; else post to Feed

    Returns:
        dict with keys: success (bool), message (str), id (media_id if success)
    """
    logger_bot = logging.getLogger('core.instagram.bot')
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        logger_bot.warning('Instagram post FAILED: not configured (set credentials or run set_instagram_token)')
        return {'success': False, 'message': 'Instagram not configured (set credentials in Settings)'}

    image_url = _path_to_public_url(image_path)
    if not image_url:
        logger_bot.warning('Instagram post FAILED: image path not accessible or not under MEDIA_ROOT')
        return {'success': False, 'message': 'Image path not accessible or not under MEDIA_ROOT'}

    if not image_url.startswith('http'):
        logger_bot.warning('Instagram post FAILED: INSTAGRAM_BASE_URL must be set for public image URL')
        return {'success': False, 'message': 'INSTAGRAM_BASE_URL must be set for public image URL'}

    caption = (caption or '')[:2200] if not is_story else ''

    payload = {
        'image_url': image_url,
        'access_token': token,
    }
    if not is_story and caption:
        payload['caption'] = caption
    if is_story:
        payload['media_type'] = 'STORIES'

    create_url = f'{GRAPH_API_BASE}/{ig_user_id}/media'
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.post(create_url, data=payload, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            container_id = data.get('id')
            if not container_id:
                err = data.get('error', {})
                return {'success': False, 'message': err.get('message', 'No container id')}

            publish_url = f'{GRAPH_API_BASE}/{ig_user_id}/media_publish'
            r2 = requests.post(
                publish_url,
                data={'creation_id': container_id, 'access_token': token},
                timeout=REQUEST_TIMEOUT,
            )
            r2.raise_for_status()
            pub = r2.json()
            media_id = pub.get('id')
            logger.info('Instagram post: media_id=%s is_story=%s', media_id, is_story)
            logger_bot.info('Instagram post SUCCESS: media_id=%s is_story=%s image_url=%s', media_id, is_story, image_url[:80])
            return {'success': True, 'message': 'Published', 'id': media_id}
        except requests.RequestException as e:
            last_error = e
            msg = str(e)
            if hasattr(e, 'response') and e.response is not None:
                try:
                    body = e.response.json()
                    msg = body.get('error', {}).get('message', msg)
                except Exception:
                    pass
            logger.warning('Instagram post attempt %d/%d: %s', attempt, MAX_RETRIES, msg)
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY_SECONDS)

    msg = str(last_error)[:500] if last_error else 'Unknown error'
    error_code = None
    if last_error:
        err_resp = getattr(last_error, 'response', None)
        if err_resp is not None and hasattr(err_resp, 'json'):
            try:
                error_code = err_resp.json().get('error', {}).get('code')
            except Exception:
                pass
    logger_bot.warning('Instagram post FAILED: %s', msg[:300])
    try:
        from core.notifications import send_notification
        notif_msg = f'Instagram post failed: {msg}'
        if error_code:
            notif_msg = f'Instagram post failed (Error {error_code}): {msg}'
        send_notification(level='error', message=notif_msg, add_to_active_errors=True)
    except Exception as notif_err:
        logger.warning('Could not send post-failure notification: %s', notif_err)
    return {'success': False, 'message': msg}
