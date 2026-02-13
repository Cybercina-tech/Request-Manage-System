"""
Iraniu — Require authentication for all internal URLs.
Only these are public: /, /login/, /logout/, /i18n/, /api/submit/, /telegram/webhook/*
"""

from django.conf import settings


# Paths (exact or prefix) that anonymous users may access
# /media/ must be public so Instagram (Meta) crawler can fetch image URLs (200 OK, no login).
PUBLIC_PATHS = (
    "/",
    "/login/",
    "/logout/",
    "/i18n/",       # Language switching (set_language) must be public
    "/api/submit/",
    "/api/v1/",     # Partner API uses X-API-KEY
    "/telegram/webhook/",
    "/media/",      # Public media for Instagram Graph API image_url (Feed + Story)
)


def _is_public(path):
    if not path:
        return True
    path = path.rstrip("/") or "/"
    for allowed in PUBLIC_PATHS:
        allowed_stripped = allowed.rstrip("/") or "/"
        if path == allowed_stripped or path.startswith(allowed_stripped + "/"):
            return True
    return False


class LoginRequiredMiddleware:
    """
    Redirect anonymous users to LOGIN_URL for any non-public path.
    Runs after AuthenticationMiddleware so request.user is available.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not _is_public(request.path):
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path(), login_url=settings.LOGIN_URL)
        return self.get_response(request)


class ApiKeyAuthMiddleware:
    """
    For /api/v1/*: validate X-API-KEY header via ApiClient (constant-time), set request.api_client,
    enforce rate limit per client and per IP. Returns 401/429 before view if invalid or throttled.
    """
    API_V1_PREFIX = '/api/v1/'
    HEADER_API_KEY = 'HTTP_X_API_KEY'
    IP_RATE_LIMIT_PER_MIN = 120  # max requests per minute per IP (in addition to per-client)

    def __init__(self, get_response):
        self.get_response = get_response

    def _get_client_ip(self, request):
        xff = request.META.get('HTTP_X_FORWARDED_FOR')
        if xff:
            return xff.split(',')[0].strip()
        return request.META.get('REMOTE_ADDR', '')[:45]

    def __call__(self, request):
        if not request.path.startswith(self.API_V1_PREFIX):
            return self.get_response(request)

        from django.http import JsonResponse
        from django.utils import timezone
        from django.core.cache import cache
        from core.models import ApiClient
        from core.encryption import verify_api_key

        request.api_client = None
        # IP throttling first
        ip = self._get_client_ip(request)
        if ip:
            now = timezone.now()
            window = now.strftime('%Y-%m-%dT%H:%M')
            ip_key = f'api_ip_rate_{ip}_{window}'
            ip_count = cache.get(ip_key, 0) + 1
            cache.set(ip_key, ip_count, timeout=120)
            if ip_count > self.IP_RATE_LIMIT_PER_MIN:
                return JsonResponse({'error': 'Rate limit exceeded', 'message': 'Too many requests from this IP.'}, status=429)

        key = (request.META.get(self.HEADER_API_KEY) or '').strip()
        if not key:
            return JsonResponse({'error': 'Missing API key', 'message': 'Provide X-API-KEY header.'}, status=401)

        client = None
        for c in ApiClient.objects.filter(is_active=True):
            if verify_api_key(key, c.api_key_hashed):
                client = c
                break
        if not client:
            return JsonResponse({'error': 'Invalid API key', 'message': 'X-API-KEY is invalid or inactive.'}, status=401)

        # Per-client rate limit
        now = timezone.now()
        window = now.strftime('%Y-%m-%dT%H:%M')
        cache_key = f'api_rate_{client.pk}_{window}'
        count = cache.get(cache_key, 0) + 1
        cache.set(cache_key, count, timeout=120)
        if count > client.rate_limit_per_min:
            return JsonResponse({'error': 'Rate limit exceeded', 'message': f'Max {client.rate_limit_per_min} requests per minute.'}, status=429)

        client.last_used_at = now
        client.save(update_fields=['last_used_at'])
        request.api_client = client
        return self.get_response(request)
