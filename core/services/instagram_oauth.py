"""
Iraniu — Instagram OAuth 2.0 service.

Handles the full Meta OAuth flow:
  1. Generate authorization URL with CSRF state param
  2. Exchange authorization code → short-lived user token
  3. Exchange short-lived → long-lived token (60 days)
  4. Resolve Instagram Business Account ID from linked Facebook Page
  5. Persist encrypted token + expiry in SiteConfiguration
"""

import logging
import secrets
from datetime import timedelta

import requests
from django.utils import timezone

logger = logging.getLogger('core.services.instagram')

GRAPH_API_VERSION = 'v18.0'
GRAPH_API_BASE = f'https://graph.facebook.com/{GRAPH_API_VERSION}'
FACEBOOK_OAUTH_URL = f'https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth'
REQUEST_TIMEOUT = 15

# Permissions required for our Instagram integration
INSTAGRAM_SCOPES = [
    'instagram_basic',
    'instagram_content_publish',
    'pages_show_list',
    'pages_read_engagement',
]


def generate_oauth_state() -> str:
    """Generate a cryptographically secure random state token for CSRF protection."""
    return secrets.token_urlsafe(48)


def build_authorization_url(app_id: str, redirect_uri: str, state: str) -> str:
    """
    Build the Meta OAuth authorization URL.

    The user is redirected here to grant permissions. Meta redirects back to
    ``redirect_uri`` with ``?code=AUTH_CODE&state=STATE``.
    """
    params = {
        'client_id': app_id,
        'redirect_uri': redirect_uri,
        'scope': ','.join(INSTAGRAM_SCOPES),
        'response_type': 'code',
        'state': state,
    }
    qs = '&'.join(f'{k}={requests.utils.quote(str(v))}' for k, v in params.items())
    return f'{FACEBOOK_OAUTH_URL}?{qs}'


def exchange_code_for_short_lived_token(
    code: str,
    app_id: str,
    app_secret: str,
    redirect_uri: str,
) -> dict:
    """
    Exchange the authorization code for a short-lived user access token (1 hour).

    Returns dict: {success, access_token, token_type, expires_in, error}
    """
    try:
        r = requests.get(
            f'{GRAPH_API_BASE}/oauth/access_token',
            params={
                'client_id': app_id,
                'client_secret': app_secret,
                'redirect_uri': redirect_uri,
                'code': code,
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        if r.ok and data.get('access_token'):
            logger.info('Instagram OAuth: exchanged code for short-lived token.')
            return {
                'success': True,
                'access_token': data['access_token'],
                'token_type': data.get('token_type', 'bearer'),
                'expires_in': data.get('expires_in', 3600),
            }
        error_msg = data.get('error', {}).get('message', r.text[:300])
        logger.warning('Instagram OAuth code exchange failed: %s', error_msg)
        return {'success': False, 'error': error_msg}
    except requests.RequestException as e:
        logger.exception('Instagram OAuth code exchange error: %s', e)
        return {'success': False, 'error': str(e)[:300]}


def exchange_for_long_lived_token(
    short_lived_token: str,
    app_id: str,
    app_secret: str,
) -> dict:
    """
    Exchange a short-lived token for a long-lived access token (60 days).

    Returns dict: {success, access_token, token_type, expires_in, error}
    """
    try:
        r = requests.get(
            f'{GRAPH_API_BASE}/oauth/access_token',
            params={
                'grant_type': 'fb_exchange_token',
                'client_id': app_id,
                'client_secret': app_secret,
                'fb_exchange_token': short_lived_token,
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        if r.ok and data.get('access_token'):
            expires_in = data.get('expires_in', 5184000)  # default 60 days
            logger.info('Instagram OAuth: exchanged for long-lived token (expires_in=%s).', expires_in)
            return {
                'success': True,
                'access_token': data['access_token'],
                'token_type': data.get('token_type', 'bearer'),
                'expires_in': expires_in,
            }
        error_msg = data.get('error', {}).get('message', r.text[:300])
        logger.warning('Instagram OAuth long-lived exchange failed: %s', error_msg)
        return {'success': False, 'error': error_msg}
    except requests.RequestException as e:
        logger.exception('Instagram OAuth long-lived exchange error: %s', e)
        return {'success': False, 'error': str(e)[:300]}


def resolve_instagram_business_id(access_token: str) -> dict:
    """
    Find the Instagram Business Account ID from the user's Facebook Pages.

    Returns dict: {success, ig_user_id, page_id, page_name, error}
    """
    try:
        r = requests.get(
            f'{GRAPH_API_BASE}/me/accounts',
            params={
                'access_token': access_token,
                'fields': 'id,name,instagram_business_account',
            },
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        if not r.ok:
            error_msg = data.get('error', {}).get('message', r.text[:300])
            return {'success': False, 'error': error_msg}

        pages = data.get('data', [])
        for page in pages:
            ig_account = page.get('instagram_business_account')
            if ig_account and ig_account.get('id'):
                logger.info(
                    'Instagram OAuth: resolved ig_user_id=%s from page=%s (%s).',
                    ig_account['id'], page.get('id'), page.get('name'),
                )
                return {
                    'success': True,
                    'ig_user_id': ig_account['id'],
                    'page_id': page.get('id', ''),
                    'page_name': page.get('name', ''),
                }
        return {
            'success': False,
            'error': 'No Facebook Page with a linked Instagram Business account found. '
                     'Ensure your Instagram account is a Business or Creator account linked to a Facebook Page.',
        }
    except requests.RequestException as e:
        logger.exception('Instagram OAuth resolve business ID error: %s', e)
        return {'success': False, 'error': str(e)[:300]}


def check_token_permissions(access_token: str) -> dict:
    """
    Dry-run: check which permissions the current token has.

    Returns dict: {success, permissions: list[str], has_publish: bool, error}
    """
    try:
        r = requests.get(
            f'{GRAPH_API_BASE}/me/permissions',
            params={'access_token': access_token},
            timeout=REQUEST_TIMEOUT,
        )
        data = r.json()
        if not r.ok:
            error_msg = data.get('error', {}).get('message', r.text[:300])
            return {'success': False, 'error': error_msg}

        perms = data.get('data', [])
        granted = [p['permission'] for p in perms if p.get('status') == 'granted']
        has_publish = 'instagram_content_publish' in granted
        logger.info('Instagram token permissions: %s (has_publish=%s)', granted, has_publish)
        return {
            'success': True,
            'permissions': granted,
            'has_publish': has_publish,
        }
    except requests.RequestException as e:
        logger.exception('Instagram check_token_permissions error: %s', e)
        return {'success': False, 'error': str(e)[:300]}


def perform_full_oauth_exchange(
    code: str,
    redirect_uri: str,
) -> dict:
    """
    Full OAuth exchange: code → short-lived → long-lived token.

    Reads APP_ID and APP_SECRET from the database (SiteConfiguration).
    On success, stores the encrypted token + expiry and resolves IG Business ID.

    Returns dict: {success, message, ig_user_id, expires_at, permissions, error}
    """
    from core.models import SiteConfiguration

    config = SiteConfiguration.get_config()
    app_id = (config.instagram_app_id or '').strip()
    app_secret = config.get_instagram_app_secret()

    if not app_id or not app_secret:
        return {
            'success': False,
            'error': 'Instagram App ID or App Secret is not configured. Save them first in the Instagram Settings card.',
        }

    # Step 1: code → short-lived token
    step1 = exchange_code_for_short_lived_token(code, app_id, app_secret, redirect_uri)
    if not step1.get('success'):
        return {'success': False, 'error': f"Code exchange failed: {step1.get('error', 'Unknown')}"}

    short_token = step1['access_token']

    # Step 2: short-lived → long-lived token
    step2 = exchange_for_long_lived_token(short_token, app_id, app_secret)
    if not step2.get('success'):
        return {'success': False, 'error': f"Long-lived exchange failed: {step2.get('error', 'Unknown')}"}

    long_token = step2['access_token']
    expires_in = step2.get('expires_in', 5184000)
    expires_at = timezone.now() + timedelta(seconds=expires_in)

    # Step 3: Resolve Instagram Business Account ID
    step3 = resolve_instagram_business_id(long_token)
    ig_user_id = step3.get('ig_user_id', '')
    page_name = step3.get('page_name', '')

    # Step 4: Check permissions (non-blocking)
    perms_result = check_token_permissions(long_token)
    permissions = perms_result.get('permissions', [])

    # Step 5: Persist everything
    config.set_facebook_access_token(long_token)
    config.instagram_token_expires_at = expires_at
    config.instagram_oauth_state = ''  # Clear used state

    update_fields = [
        'facebook_access_token_encrypted',
        'instagram_token_expires_at',
        'instagram_oauth_state',
    ]

    if ig_user_id:
        config.instagram_business_id = ig_user_id
        update_fields.append('instagram_business_id')

    config.save(update_fields=update_fields)

    # Invalidate dashboard cache
    try:
        from django.core.cache import cache
        cache.delete('dashboard_instagram_valid')
        cache.delete('site_config_singleton')
    except Exception:
        pass

    # Build success message
    parts = ['Instagram connected successfully.']
    if page_name:
        parts.append(f'Page: {page_name}.')
    if ig_user_id:
        parts.append(f'IG ID: {ig_user_id}.')
    parts.append(f'Token expires: {expires_at.strftime("%Y-%m-%d %H:%M")}.')
    if permissions:
        parts.append(f'Permissions: {", ".join(permissions)}.')
    if not perms_result.get('has_publish'):
        parts.append('WARNING: instagram_content_publish not granted — posting will fail.')

    logger.info('Instagram OAuth complete: ig_user_id=%s, expires_at=%s', ig_user_id, expires_at)

    return {
        'success': True,
        'message': ' '.join(parts),
        'ig_user_id': ig_user_id,
        'expires_at': expires_at,
        'permissions': permissions,
    }
