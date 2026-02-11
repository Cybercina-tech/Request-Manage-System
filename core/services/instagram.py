"""
Iraniu — Instagram (Meta Graph API) service. Post approved ads; tokens encrypted at rest.
No hardcoded secrets; config from InstagramConfiguration.
Supports retry on network errors, bilingual captions (EN/FA), message+email+phone in caption.
"""

import logging
import time

import requests

from core.models import AdRequest, InstagramConfiguration

logger = logging.getLogger(__name__)

GRAPH_API_BASE = 'https://graph.facebook.com/v18.0'
REQUEST_TIMEOUT = 15
MAX_RETRIES = 2
RETRY_DELAY_SECONDS = 0.5
INSTAGRAM_CAPTION_MAX_LENGTH = 2200


def validate_instagram_token(token: str) -> tuple[bool, str]:
    """
    Validate a Facebook/Instagram access token via Graph API (e.g. for SiteConfiguration).
    Returns (success, message). Does not require InstagramConfiguration.
    """
    if not (token or '').strip():
        return False, 'No access token'
    try:
        r = requests.get(
            f'{GRAPH_API_BASE}/me',
            params={'access_token': token.strip(), 'fields': 'id,name'},
            timeout=REQUEST_TIMEOUT,
        )
        r.raise_for_status()
        data = r.json()
        if data.get('id'):
            return True, f"Connected as {data.get('name', data['id'])}"
        return False, 'Invalid response'
    except requests.RequestException as e:
        logger.exception('Instagram validate_instagram_token: %s', e)
        err = getattr(e, 'response', None)
        if err is not None and hasattr(err, 'json'):
            try:
                body = err.json()
                return False, body.get('error', {}).get('message', str(e))
            except Exception:
                pass
        return False, str(e)[:200]
    except Exception as e:
        logger.exception('Instagram validate_instagram_token: %s', e)
        return False, str(e)[:200]


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
    def format_caption(
        ad: AdRequest,
        max_length: int = INSTAGRAM_CAPTION_MAX_LENGTH,
        lang: str = 'en',
        include_emojis: bool = True,
    ) -> str:
        """
        Build Instagram caption: message text, email, phone. Truncates to max_length.
        Uses contact_snapshot for email/phone. Bilingual (en/fa).
        """
        if not ad:
            return ''
        parts = []
        if ad.category:
            cat_label = ad.get_category_display()
            if include_emojis:
                parts.append(f'📂 [{cat_label}]')
            else:
                parts.append(f'[{cat_label}]')
        parts.append(ad.content or '')
        contact = getattr(ad, 'contact_snapshot', None) or {}
        email = (contact.get('email') or '').strip()
        phone = (contact.get('phone') or '').strip()
        if email or phone:
            contact_lines = []
            if lang == 'fa':
                if email:
                    contact_lines.append(f'📧 {email}')
                if phone:
                    contact_lines.append(f'📞 {phone}')
            else:
                if email:
                    contact_lines.append(f'📧 {email}')
                if phone:
                    contact_lines.append(f'📞 {phone}')
            if contact_lines:
                parts.append('\n'.join(contact_lines))
        if include_emojis and lang == 'en':
            parts.append('🙏 Iraniu — trusted classifieds')
        elif include_emojis and lang == 'fa':
            parts.append('🙏 ایرانيو — آگهی‌های معتبر')
        caption = '\n\n'.join(p for p in parts if p).strip()
        if not caption:
            return ''
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

        lang = 'en'
        if ad.user_id:
            from core.models import TelegramSession
            session = TelegramSession.objects.filter(
                telegram_user_id=ad.telegram_user_id,
                bot_id=ad.bot_id,
            ).first()
            if session and session.language == 'fa':
                lang = 'fa'
        caption = InstagramService.format_caption(ad, lang=lang)

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
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
                    err_body = data.get('error', {})
                    return {'success': False, 'message': err_body.get('message', 'No container id')}

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
                last_error = e
                err = getattr(e, 'response', None)
                msg = str(e)
                if err is not None and hasattr(err, 'json'):
                    try:
                        body = err.json()
                        msg = body.get('error', {}).get('message', msg)
                    except Exception:
                        pass
                logger.warning('Instagram post_ad attempt %d/%d failed: %s', attempt, MAX_RETRIES, msg)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS)
            except Exception as e:
                last_error = e
                logger.exception('Instagram post_ad attempt %d/%d: %s', attempt, MAX_RETRIES, e)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS)

        err = last_error or Exception('Unknown error')
        err_resp = getattr(err, 'response', None)
        msg = str(err)[:500]
        if err_resp is not None and hasattr(err_resp, 'json'):
            try:
                body = err_resp.json()
                msg = body.get('error', {}).get('message', msg)[:500]
            except Exception:
                pass
        logger.exception('Instagram post_ad: all retries failed: %s', msg)
        return {'success': False, 'message': msg}

    @staticmethod
    def post_custom(
        image_url: str,
        caption: str,
        config: InstagramConfiguration | None = None,
    ) -> dict:
        """
        Post custom content to Instagram. Returns dict with success, message, id.
        image_url must be publicly accessible. caption truncated to 2200 chars.
        """
        if not (image_url or '').strip():
            return {'success': False, 'message': 'No image URL'}
        config = config or InstagramConfiguration.objects.filter(is_active=True).first()
        if not config:
            return {'success': False, 'message': 'Instagram not configured'}
        token = config.get_decrypted_token()
        if not (token or '').strip():
            return {'success': False, 'message': 'No access token'}
        ig_user_id = (config.ig_user_id or '').strip()
        if not ig_user_id:
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
                logger.warning('Instagram post_custom: could not resolve ig_user_id: %s', e)
            if not ig_user_id:
                return {'success': False, 'message': 'Instagram user ID not set'}

        caption = (caption or '')[:INSTAGRAM_CAPTION_MAX_LENGTH]
        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                create_url = f'{GRAPH_API_BASE}/{ig_user_id}/media'
                payload = {'image_url': image_url, 'caption': caption, 'access_token': token}
                r = requests.post(create_url, data=payload, timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                data = r.json()
                container_id = data.get('id')
                if not container_id:
                    return {'success': False, 'message': data.get('error', {}).get('message', 'No container id')}
                r2 = requests.post(
                    f'{GRAPH_API_BASE}/{ig_user_id}/media_publish',
                    data={'creation_id': container_id, 'access_token': token},
                    timeout=REQUEST_TIMEOUT,
                )
                r2.raise_for_status()
                media_id = r2.json().get('id')
                logger.info('Instagram post_custom: published media_id=%s', media_id)
                return {'success': True, 'message': 'Published', 'id': media_id}
            except requests.RequestException as e:
                last_error = e
                msg = str(e)
                if hasattr(e, 'response') and e.response is not None:
                    try:
                        msg = e.response.json().get('error', {}).get('message', msg)
                    except Exception:
                        pass
                logger.warning('Instagram post_custom attempt %d/%d: %s', attempt, MAX_RETRIES, msg)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS)
            except Exception as e:
                last_error = e
                logger.exception('Instagram post_custom attempt %d/%d: %s', attempt, MAX_RETRIES, e)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_DELAY_SECONDS)
        msg = str(last_error)[:500] if last_error else 'Unknown error'
        return {'success': False, 'message': msg}
