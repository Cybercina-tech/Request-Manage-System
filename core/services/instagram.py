"""
Iranio — Instagram (Meta Graph API) service. Post approved ads; tokens encrypted at rest.
No hardcoded secrets; config from InstagramConfiguration.
"""

import logging

import requests

from core.models import AdRequest, InstagramConfiguration

logger = logging.getLogger(__name__)

GRAPH_API_BASE = 'https://graph.facebook.com/v18.0'
REQUEST_TIMEOUT = 30


class InstagramService:
    """Post ad to Instagram via Graph API. Validate credentials; format caption; optional image."""

    @staticmethod
    def validate_credentials(config: InstagramConfiguration) -> tuple[bool, str]:
        """
        Validate Instagram/Meta access token. Returns (success, message).
        Updates config.last_test_at on success.
        """
        if not config:
            return False, 'No configuration'
        token = config.get_decrypted_token()
        if not (token or '').strip():
            return False, 'No access token'
        try:
            r = requests.get(
                f'{GRAPH_API_BASE}/me',
                params={'access_token': token, 'fields': 'id,name'},
                timeout=REQUEST_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            if data.get('id'):
                from django.utils import timezone
                config.last_test_at = timezone.now()
                config.save(update_fields=['last_test_at'])
                return True, f"Connected as {data.get('name', data['id'])}"
            return False, 'Invalid response'
        except requests.RequestException as e:
            logger.exception('Instagram validate_credentials: %s', e)
            err = getattr(e, 'response', None)
            if err is not None and hasattr(err, 'json'):
                try:
                    body = err.json()
                    return False, body.get('error', {}).get('message', str(e))
                except Exception:
                    pass
            return False, str(e)[:200]
        except Exception as e:
            logger.exception('Instagram validate_credentials: %s', e)
            return False, str(e)[:200]

    @staticmethod
    def format_caption(ad: AdRequest, max_length: int = 2200) -> str:
        """Build Instagram caption from ad content and category. Truncates to max_length."""
        if not ad:
            return ''
        parts = []
        if ad.category:
            parts.append(f"[{ad.get_category_display()}]")
        parts.append(ad.content or '')
        caption = '\n\n'.join(p for p in parts if p).strip()
        if not caption:
            return ''
        # Instagram caption limit 2200
        if len(caption) > max_length:
            caption = caption[: max_length - 3] + '...'
        return caption

    @staticmethod
    def _get_image_url(ad: AdRequest, config: InstagramConfiguration) -> str | None:
        """Resolve image URL for container: from ad (future) or config placeholder."""
        # Future: if ad has media_url or similar, use it
        if getattr(ad, 'media_url', None):
            return (ad.media_url or '').strip() or None
        if config and config.placeholder_image_url:
            return config.placeholder_image_url.strip() or None
        return None

    @staticmethod
    def upload_image_if_needed(ad: AdRequest, config: InstagramConfiguration) -> str | None:
        """
        Return image URL to use for this ad (no actual upload to Meta here; we pass URL to container).
        Returns None if no image available; caller may skip Instagram or use placeholder.
        """
        return InstagramService._get_image_url(ad, config)

    @staticmethod
    def post_ad(ad: AdRequest) -> dict:
        """
        Post ad to Instagram using first active InstagramConfiguration.
        Returns dict with keys: success (bool), message (str), id (optional).
        Uses caption + optional image URL (placeholder if no ad image).
        """
        if not ad or ad.status != AdRequest.Status.APPROVED:
            return {'success': False, 'message': 'Ad not approved'}
        config = InstagramConfiguration.objects.filter(is_active=True).first()
        if not config:
            logger.info('Instagram post_ad: no active config')
            return {'success': False, 'message': 'Instagram not configured'}
        token = config.get_decrypted_token()
        if not (token or '').strip():
            return {'success': False, 'message': 'No access token'}
        ig_user_id = (config.ig_user_id or '').strip()
        if not ig_user_id:
            # Try to get from me
            try:
                r = requests.get(
                    f'{GRAPH_API_BASE}/me',
                    params={'access_token': token, 'fields': 'id'},
                    timeout=REQUEST_TIMEOUT,
                )
                if r.ok:
                    ig_user_id = r.json().get('id', '')
                    if ig_user_id:
                        config.ig_user_id = ig_user_id
                        config.save(update_fields=['ig_user_id'])
            except Exception as e:
                logger.warning('Instagram post_ad: could not resolve ig_user_id: %s', e)
            if not ig_user_id:
                return {'success': False, 'message': 'Instagram user ID not set'}

        image_url = InstagramService.upload_image_if_needed(ad, config)
        if not image_url:
            return {'success': False, 'message': 'No image URL (set placeholder in Instagram settings)'}

        caption = InstagramService.format_caption(ad)
        try:
            # 1) Create container
            create_url = f'{GRAPH_API_BASE}/{ig_user_id}/media'
            payload = {
                'image_url': image_url,
                'caption': caption,
                'access_token': token,
            }
            r = requests.post(create_url, data=payload, timeout=REQUEST_TIMEOUT)
            r.raise_for_status()
            data = r.json()
            container_id = data.get('id')
            if not container_id:
                return {'success': False, 'message': data.get('error', {}).get('message', 'No container id')}

            # 2) Publish
            publish_url = f'{GRAPH_API_BASE}/{ig_user_id}/media_publish'
            r2 = requests.post(
                publish_url,
                data={'creation_id': container_id, 'access_token': token},
                timeout=REQUEST_TIMEOUT,
            )
            r2.raise_for_status()
            pub = r2.json()
            media_id = pub.get('id')
            logger.info('Instagram post_ad: published ad=%s media_id=%s', ad.uuid, media_id)
            return {'success': True, 'message': 'Published', 'id': media_id}
        except requests.RequestException as e:
            logger.exception('Instagram post_ad: %s', e)
            err = getattr(e, 'response', None)
            msg = str(e)
            if err is not None and hasattr(err, 'json'):
                try:
                    body = err.json()
                    msg = body.get('error', {}).get('message', msg)
                except Exception:
                    pass
            return {'success': False, 'message': msg[:500]}
        except Exception as e:
            logger.exception('Instagram post_ad: %s', e)
            return {'success': False, 'message': str(e)[:500]}
