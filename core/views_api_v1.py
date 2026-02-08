"""
Iraniu — Partner API v1. Submit, status, list. Auth via X-API-KEY (middleware).
All views assume request.api_client is set (middleware returns 401 otherwise).
"""

import logging
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.shortcuts import get_object_or_404

from core.models import AdRequest, Category, SiteConfiguration
from core.view_utils import parse_request_json
from core.services import clean_ad_text, run_ai_moderation

logger = logging.getLogger(__name__)


def _get_valid_category_slugs():
    return set(Category.objects.filter(is_active=True).values_list('slug', flat=True))


def _sanitize_contact(data: dict) -> dict:
    """Extract and sanitize contact fields for contact_snapshot."""
    if not isinstance(data, dict):
        return {}
    return {
        'email': (data.get('email') or '').strip()[:254],
        'phone': (data.get('phone') or '').strip()[:20],
    }


@csrf_exempt
@require_http_methods(['POST'])
def api_v1_submit(request):
    """
    POST /api/v1/submit/
    Body: { "content": "...", "category": "...", "contact": { "email": "...", "phone": "..." } }
    Returns 201 { "uuid", "status" } or 400/500.
    """
    if not getattr(request, 'api_client', None):
        return JsonResponse({'error': 'Unauthorized', 'message': 'Invalid or missing API key.'}, status=401)
    try:
        data = parse_request_json(request) or request.POST.dict()
        content = (data.get('content') or '').strip()
        if not content:
            return JsonResponse({'error': 'Validation error', 'message': 'content is required.'}, status=400)
        content = clean_ad_text(content)
        slug = (data.get('category') or 'other').strip()
        valid = _get_valid_category_slugs()
        if slug not in valid:
            slug = 'other'
        category = Category.objects.filter(slug=slug, is_active=True).first() or Category.objects.filter(slug='other').first()
        contact = _sanitize_contact(data.get('contact') or {})

        config = SiteConfiguration.get_config()
        ad = AdRequest.objects.create(
            content=content,
            category=category,
            status=AdRequest.Status.PENDING_AI,
            submitted_via_api_client=request.api_client,
            contact_snapshot=contact,
        )
        if config.is_ai_enabled:
            approved, reason = run_ai_moderation(ad.content, config)
            ad.status = AdRequest.Status.PENDING_MANUAL
            if not approved and reason:
                ad.ai_suggested_reason = reason[:500]
            ad.save(update_fields=['status', 'ai_suggested_reason'])
        else:
            ad.status = AdRequest.Status.PENDING_MANUAL
            ad.save(update_fields=['status'])

        logger.info('API v1 submit: ad=%s client=%s', ad.uuid, request.api_client.name)
        return JsonResponse({'uuid': str(ad.uuid), 'status': ad.status}, status=201)
    except Exception as e:
        logger.exception('API v1 submit: %s', e)
        return JsonResponse({'error': 'Server error', 'message': 'Submission failed.'}, status=500)


@require_http_methods(['GET'])
def api_v1_status(request, uuid):
    """
    GET /api/v1/status/<uuid>/
    Returns 200 { "uuid", "status", "category", "created_at" } for ads submitted by this client.
    """
    if not getattr(request, 'api_client', None):
        return JsonResponse({'error': 'Unauthorized', 'message': 'Invalid or missing API key.'}, status=401)
    ad = get_object_or_404(AdRequest, uuid=uuid)
    if ad.submitted_via_api_client_id != request.api_client.pk:
        return JsonResponse({'error': 'Not found', 'message': 'Ad not found or access denied.'}, status=404)
    return JsonResponse({
        'uuid': str(ad.uuid),
        'status': ad.status,
        'category': ad.category.slug if ad.category else None,
        'created_at': ad.created_at.isoformat() if ad.created_at else None,
    })


@require_http_methods(['GET'])
def api_v1_list(request):
    """
    GET /api/v1/list/?status=...&category=...&limit=...&offset=...
    Returns 200 { "results": [...], "count" } for ads submitted by this client.
    """
    if not getattr(request, 'api_client', None):
        return JsonResponse({'error': 'Unauthorized', 'message': 'Invalid or missing API key.'}, status=401)
    qs = AdRequest.objects.filter(submitted_via_api_client=request.api_client).order_by('-created_at')
    status = request.GET.get('status', '').strip()
    if status and status in dict(AdRequest.Status.choices):
        qs = qs.filter(status=status)
    category = request.GET.get('category', '').strip()
    if category and category in _get_valid_category_slugs():
        qs = qs.filter(category__slug=category)
    try:
        limit = min(int(request.GET.get('limit', 50)), 100)
    except (ValueError, TypeError):
        limit = 50
    try:
        offset = max(0, int(request.GET.get('offset', 0)))
    except (ValueError, TypeError):
        offset = 0
    count = qs.count()
    page = qs[offset:offset + limit]
    results = [
        {
            'uuid': str(ad.uuid),
            'status': ad.status,
            'category': ad.category.slug if ad.category else None,
            'created_at': ad.created_at.isoformat() if ad.created_at else None,
        }
        for ad in page
    ]
    return JsonResponse({'results': results, 'count': count})
