"""
Iraniu — Staff-only views. Request/response only; business logic in services.
"""

import logging
import os
import signal
import subprocess
import sys
import uuid
from urllib.parse import quote
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator

from core.models import (
    AdRequest,
    AdTemplate,
    AdminProfile,
    Category,
    SiteConfiguration,
    TelegramBot,
    TelegramChannel,
    TelegramUser,
    InstagramConfiguration,
    ApiClient,
    DeliveryLog,
    ScheduledInstagramPost,
    REJECTION_REASONS,
    REJECTION_REASONS_DETAIL,
)
from core.services import (
    clean_ad_text,
    run_ai_moderation,
    test_telegram_connection,
    test_openai_connection,
    validate_ad_content,
    get_webhook_info,
    set_webhook,
    delete_webhook,
)
from core.services.dashboard import get_dashboard_context, get_pulse_data
from core.services.ad_actions import approve_one_ad, reject_one_ad, request_revision_one_ad
from core.view_utils import get_request_payload
from core.forms import AdTemplateCreateForm, TemplateTesterForm, ChannelForm

logger = logging.getLogger(__name__)

# Session key for passing uploaded test background to Coordinate Lab
COORD_LAB_TEMP_BACKGROUND_KEY = "coord_lab_temp_background_path"


def landing(request):
    """Landing page — minimal staff-only gateway. Authenticated users go to dashboard."""
    if request.user.is_authenticated:
        return redirect('dashboard')
    return render(request, 'core/landing.html')


@staff_member_required
def dashboard(request):
    """Analytics: KPI cards and bird's-eye view. Data from services.dashboard."""
    context = get_dashboard_context()
    return render(request, 'core/dashboard.html', context)


@staff_member_required
@require_http_methods(['GET'])
def api_pulse(request):
    """Live stats for dashboard polling. Data from services.dashboard."""
    return JsonResponse(get_pulse_data())


@staff_member_required
def ad_list(request):
    """New Requests workspace: triage list with filters and pagination."""
    qs = AdRequest.objects.select_related('category').order_by('-created_at')
    # Optional: .only() for list to avoid loading full content
    category = request.GET.get('category')
    status = request.GET.get('status')
    date_from = request.GET.get('date_from')
    date_to = request.GET.get('date_to')
    search = request.GET.get('search', '').strip()

    if category:
        qs = qs.filter(category__slug=category)
    if status:
        qs = qs.filter(status=status)
    if date_from:
        qs = qs.filter(created_at__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_at__date__lte=date_to)
    if search:
        qs = qs.filter(content__icontains=search)

    paginator = Paginator(qs, 20)
    page = request.GET.get('page', 1)
    page_obj = paginator.get_page(page)

    # Quick stats for mini status bar
    total_pending = AdRequest.objects.filter(
        status__in=[AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL]
    ).count()
    flagged_by_ai = AdRequest.objects.filter(
        status=AdRequest.Status.PENDING_MANUAL,
        ai_suggested_reason__isnull=False
    ).exclude(ai_suggested_reason='').count()
    # High priority: pending for more than 24 hours
    high_priority = AdRequest.objects.filter(
        status__in=[AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL],
        created_at__lt=timezone.now() - timedelta(hours=24)
    ).count()

    category_choices = [(c.slug, c.name) for c in Category.objects.filter(is_active=True).order_by('order', 'name')]
    context = {
        'page_obj': page_obj,
        'rejection_reasons': REJECTION_REASONS,
        'category_choices': category_choices,
        'status_choices': AdRequest.Status.choices,
        'quick_stats': {
            'pending': total_pending,
            'flagged_by_ai': flagged_by_ai,
            'high_priority': high_priority,
        },
        'filters': {
            'category': category,
            'status': status,
            'date_from': date_from,
            'date_to': date_to,
            'search': request.GET.get('search', ''),
        },
    }
    return render(request, 'core/ad_list.html', context)


@staff_member_required
def ad_detail(request, uuid):
    """
    Request Detail: ad content (read-only display), client info (read-only),
    predefined rejection dropdown, approve with confirmation / reject with reason.
    Passes client (TelegramUser), rejection reasons list, and AI suggested reason for template.
    """
    ad = get_object_or_404(AdRequest, uuid=uuid)
    # Client: linked TelegramUser (ad.user) or lookup by telegram_user_id for read-only display
    client = None
    if getattr(ad, 'user_id', None) and ad.user_id:
        client = ad.user
    elif ad.telegram_user_id:
        client = TelegramUser.objects.filter(telegram_user_id=ad.telegram_user_id).first()
    context = {
        'ad': ad,
        'client': client,
        'ai_suggested_reason': ad.ai_suggested_reason or '',
    }
    return render(request, 'core/ad_detail.html', context)


@staff_member_required
def request_detail(request, uuid):
    """Standalone request detail page (no modal). Same as ad_detail."""
    ad = get_object_or_404(AdRequest, uuid=uuid)
    client = None
    if getattr(ad, 'user_id', None) and ad.user_id:
        client = ad.user
    elif ad.telegram_user_id:
        client = TelegramUser.objects.filter(telegram_user_id=ad.telegram_user_id).first()
    context = {'ad': ad, 'client': client, 'ai_suggested_reason': ad.ai_suggested_reason or ''}
    return render(request, 'core/request_detail.html', context)


@staff_member_required
def confirm_approve(request, uuid):
    """
    Confirm approval page. GET: show confirmation; POST: approve ad via approve_one_ad.
    Staff-only. Redirects to detail page after success.
    """
    ad = get_object_or_404(AdRequest, uuid=uuid)
    if ad.status not in (AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL):
        return redirect('ad_detail', uuid=ad.uuid)
    client = None
    if getattr(ad, 'user_id', None) and ad.user_id:
        client = ad.user
    elif ad.telegram_user_id:
        client = TelegramUser.objects.filter(telegram_user_id=ad.telegram_user_id).first()
    if request.method == 'POST':
        approve_one_ad(ad, approved_by=request.user)
        logger.info(
            "Ad approved: uuid=%s by=%s at=%s",
            ad.uuid,
            getattr(request.user, 'username', None) or getattr(request.user, 'id', None),
            timezone.now(),
        )
        return redirect('ad_detail', uuid=ad.uuid)
    context = {'ad': ad, 'client': client}
    return render(request, 'core/confirm_approve.html', context)


@staff_member_required
def confirm_reject(request, uuid):
    """
    Confirm rejection page. GET: show form; POST: reject via reject_one_ad.
    Staff-only. Validates ad exists, status is pending, not already processed.
    """
    ad = get_object_or_404(AdRequest, uuid=uuid)
    if ad.status not in (AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL):
        return redirect('ad_detail', uuid=ad.uuid)
    client = None
    if getattr(ad, 'user_id', None) and ad.user_id:
        client = ad.user
    elif ad.telegram_user_id:
        client = TelegramUser.objects.filter(telegram_user_id=ad.telegram_user_id).first()
    if request.method == 'POST':
        reason = (request.POST.get('reason') or '').strip()
        comment = (request.POST.get('comment') or '').strip()
        if not reason:
            context = {
                'ad': ad,
                'client': client,
                'rejection_reasons_detail': REJECTION_REASONS_DETAIL,
                'error': 'Rejection reason is required.',
            }
            return render(request, 'core/confirm_reject.html', context)
        labels_by_key = dict(REJECTION_REASONS_DETAIL)
        stored_reason = labels_by_key.get(reason, reason)
        if reason == 'other' and comment:
            stored_reason = f"Other: {comment}"
        reject_one_ad(ad, stored_reason, rejected_by=request.user)
        logger.info(
            "Ad rejected: uuid=%s reason=%s by=%s at=%s",
            ad.uuid,
            stored_reason[:50],
            getattr(request.user, 'username', None) or getattr(request.user, 'id', None),
            timezone.now(),
        )
        return redirect('ad_detail', uuid=ad.uuid)
    context = {
        'ad': ad,
        'client': client,
        'rejection_reasons_detail': REJECTION_REASONS_DETAIL,
    }
    return render(request, 'core/confirm_reject.html', context)


@staff_member_required
def confirm_request_revision(request, uuid):
    """
    Request revision: set ad to NEEDS_REVISION and send Telegram with Edit & Resubmit button.
    GET: show confirm; POST: perform action.
    """
    ad = get_object_or_404(AdRequest, uuid=uuid)
    if ad.status not in (AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL):
        return redirect('ad_detail', uuid=ad.uuid)
    if request.method == 'POST':
        request_revision_one_ad(ad, requested_by=request.user)
        return redirect('ad_detail', uuid=ad.uuid)
    return render(request, 'core/confirm_request_revision.html', {'ad': ad})


@staff_member_required
@require_http_methods(['GET', 'POST'])
def preview_publish(request, uuid):
    """
    Final Preview & Publish: show generated image, Instagram caption and Telegram preview,
    then "Confirm & Distribute" to run post_manager.distribute_ad.
    """
    ad = get_object_or_404(AdRequest, uuid=uuid)
    if ad.status != AdRequest.Status.APPROVED:
        messages.warning(request, 'Only approved ads can be distributed.')
        return redirect('ad_detail', uuid=ad.uuid)

    if request.method == 'POST':
        from core.services.post_manager import distribute_ad
        ok = distribute_ad(ad)
        if ok:
            messages.success(request, 'Ad distributed to Telegram and Instagram.')
        else:
            messages.error(request, 'Distribution failed. Check logs and channel/bot configuration.')
        return redirect('ad_detail', uuid=ad.uuid)

    # GET: build preview data (same logic as post_manager.distribute_ad)
    from core.services.image_engine import create_ad_image
    from core.services.instagram_api import _path_to_public_url
    from core.services.instagram import InstagramService
    from core.services.post_manager import get_default_channel

    category = ad.get_category_display() if hasattr(ad, 'get_category_display') else (ad.category.name if ad.category else 'Other')
    text = (ad.content or '').strip()
    contact = getattr(ad, 'contact_snapshot', None) or {}
    phone = (contact.get('phone') or '').strip() if isinstance(contact, dict) else ''
    if not phone and getattr(ad, 'user_id', None) and ad.user:
        phone = (ad.user.phone_number or '').strip()

    preview_image_url = None
    template = AdTemplate.objects.filter(is_active=True).first()
    if template:
        feed_path = create_ad_image(template.pk, category, text, phone)
        preview_image_url = _path_to_public_url(feed_path) if feed_path else None

    caption_preview = InstagramService.format_caption(ad, lang='fa')
    telegram_caption = f"{category}\n\n{text}"
    if phone:
        telegram_caption += f"\n\n📱 {phone}"
    telegram_preview = telegram_caption[:1024]

    channel = get_default_channel()
    context = {
        'ad': ad,
        'preview_image_url': preview_image_url,
        'caption_preview': caption_preview,
        'telegram_preview': telegram_preview,
        'default_channel': channel,
    }
    return render(request, 'core/preview_publish.html', context)


@staff_member_required
@require_http_methods(['POST'])
def post_to_instagram_view(request, uuid, target):
    """
    Generate image from Request and post to Instagram Feed or Story.
    target: 'feed' or 'story'
    Returns JSON { success, message }.
    """
    ad = get_object_or_404(AdRequest, uuid=uuid)
    if target not in ('feed', 'story'):
        return JsonResponse({'success': False, 'message': 'Invalid target'}, status=400)
    is_story = target == 'story'

    from core.utils.image_generator import generate_request_image
    from core.services.instagram_api import post_to_instagram
    from core.services.instagram import InstagramService

    image_path = generate_request_image(ad.pk, is_story=is_story)
    if not image_path:
        return JsonResponse({'success': False, 'message': 'Failed to generate image'}, status=500)

    caption = ''
    if not is_story:
        caption = InstagramService.format_caption(ad, lang='fa')

    result = post_to_instagram(image_path=image_path, caption=caption, is_story=is_story)
    return JsonResponse(result, status=200 if result.get('success') else 500)


@staff_member_required
@require_http_methods(['GET'])
def settings_view(request):
    """Settings Hub: vertical tabs — General, API & Integrations, Delivery, Team & Security, Channels, Appearance."""
    from core.forms import ChannelForm
    from django.conf import settings as django_settings
    config = SiteConfiguration.get_config()
    env = getattr(django_settings, 'ENVIRONMENT', 'PROD')
    active_tab = (request.GET.get('tab') or 'general').strip().lower()
    if active_tab not in ('general', 'api', 'delivery', 'team', 'channels', 'appearance'):
        active_tab = 'general'

    # Channels (for Channel Manager tab)
    channels = (
        TelegramChannel.objects.filter(bot_connection__environment=env)
        .select_related('bot_connection')
        .order_by('-is_default', 'title')
    )
    channel_form = ChannelForm()

    # Admins (for Team & Security tab)
    admins = AdminProfile.objects.select_related('user').order_by('user__username') if request.user.is_superuser else []

    # Recent delivery logs (compact for Delivery tab)
    delivery_qs = DeliveryLog.objects.select_related('ad').order_by('-created_at')[:25]
    delivery_channel = request.GET.get('delivery_channel', '').strip()
    delivery_status = request.GET.get('delivery_status', '').strip()
    if delivery_channel and delivery_channel in dict(DeliveryLog.Channel.choices):
        delivery_qs = delivery_qs.filter(channel=delivery_channel)
    if delivery_status and delivery_status in dict(DeliveryLog.DeliveryStatus.choices):
        delivery_qs = delivery_qs.filter(status=delivery_status)
    delivery_logs = list(delivery_qs)

    # API clients & Instagram configs (for API & Integrations tab)
    api_clients = ApiClient.objects.all().order_by('name')
    instagram_configs = InstagramConfiguration.objects.all().order_by('username')
    bots = TelegramBot.objects.filter(environment=env).order_by('-is_default', 'name')

    context = {
        'config': config,
        'active_tab': active_tab,
        'channels': channels,
        'channel_form': channel_form,
        'admins': admins,
        'delivery_logs': delivery_logs,
        'channel_choices': DeliveryLog.Channel.choices,
        'status_choices': DeliveryLog.DeliveryStatus.choices,
        'delivery_filters': {'channel': delivery_channel, 'status': delivery_status},
        'api_clients': api_clients,
        'instagram_configs': instagram_configs,
        'bots': bots,
        'theme_preference': getattr(config, 'theme_preference', 'light') or 'light',
    }
    return render(request, 'core/settings_hub.html', context)


@staff_member_required
@require_http_methods(['POST'])
def settings_save(request):
    """Save configuration (AJAX or form)."""
    config = SiteConfiguration.get_config()
    data = request.POST
    config.is_ai_enabled = data.get('is_ai_enabled') == 'on'
    config.openai_api_key = (data.get('openai_api_key') or '').strip() or config.openai_api_key
    config.openai_model = (data.get('openai_model') or config.openai_model).strip()
    config.ai_system_prompt = data.get('ai_system_prompt') or config.ai_system_prompt
    # Telegram config moved to Bots; keep legacy fields unchanged
    config.approval_message_template = data.get('approval_message_template') or config.approval_message_template
    config.rejection_message_template = data.get('rejection_message_template') or config.rejection_message_template
    config.submission_ack_message = data.get('submission_ack_message') or config.submission_ack_message
    config.production_base_url = (data.get('production_base_url') or '').strip() or config.production_base_url
    ig_id = (data.get('instagram_business_id') or '').strip()
    if ig_id:
        config.instagram_business_id = ig_id[:64]
    fb_token = (data.get('facebook_access_token') or '').strip()
    if fb_token:
        config.set_facebook_access_token(fb_token)
    config.save()
    return JsonResponse({'status': 'success'})


@staff_member_required
@require_http_methods(['POST'])
def settings_change_password(request):
    """Change password for current user. Returns JSON for Settings Hub toast."""
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError as DjangoValidationError
    User = get_user_model()
    old = (request.POST.get('old_password') or '').strip()
    new1 = (request.POST.get('new_password1') or '').strip()
    new2 = (request.POST.get('new_password2') or '').strip()
    if not old:
        return JsonResponse({'status': 'error', 'message': 'Current password is required.'}, status=400)
    if not request.user.check_password(old):
        return JsonResponse({'status': 'error', 'message': 'Current password is incorrect.'}, status=400)
    if new1 != new2:
        return JsonResponse({'status': 'error', 'message': 'New passwords do not match.'}, status=400)
    if not new1:
        return JsonResponse({'status': 'error', 'message': 'New password is required.'}, status=400)
    try:
        validate_password(new1, request.user)
    except DjangoValidationError as e:
        return JsonResponse({'status': 'error', 'message': '; '.join(e.messages)}, status=400)
    request.user.set_password(new1)
    request.user.save(update_fields=['password'])
    return JsonResponse({'status': 'success', 'message': 'Password changed successfully.'})


@staff_member_required
@require_http_methods(['POST'])
def theme_save(request):
    """Save theme preference (light/dark) via AJAX; invalidates config cache."""
    theme = (request.POST.get('theme') or '').strip().lower()
    if theme not in ('light', 'dark'):
        return JsonResponse({'status': 'error', 'message': 'Invalid theme'}, status=400)
    config = SiteConfiguration.get_config()
    config.theme_preference = theme
    config.save()
    try:
        from django.core.cache import cache
        cache.delete('site_config_singleton')
    except Exception:
        pass
    return JsonResponse({'status': 'success', 'theme': theme})


@staff_member_required
@require_http_methods(['POST'])
def test_telegram(request):
    """Test Telegram connection (AJAX). Does not save token."""
    token = (request.POST.get('telegram_bot_token') or '').strip()
    ok, msg = test_telegram_connection(token)
    return JsonResponse({'success': ok, 'message': msg})


@staff_member_required
@require_http_methods(['POST'])
def test_openai(request):
    """Test OpenAI connection (AJAX). Does not save key."""
    key = (request.POST.get('openai_api_key') or '').strip()
    ok, msg = test_openai_connection(key)
    return JsonResponse({'success': ok, 'message': msg})


@staff_member_required
@require_http_methods(['POST'])
def approve_ad(request):
    """Approve ad: delegate to ad_actions.approve_one_ad, return JSON."""
    try:
        body = get_request_payload(request)
        ad_id = body.get('ad_id')
        edited_content = (body.get('content') or '').strip() or None
        if not ad_id:
            return JsonResponse({'status': 'error', 'message': 'Missing ad_id'}, status=400)
        ad = get_object_or_404(AdRequest, uuid=ad_id)
        if ad.status not in (AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL):
            return JsonResponse({'status': 'error', 'message': 'Ad not in pending state'}, status=400)
        approve_one_ad(ad, edited_content=edited_content, approved_by=request.user)
        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@staff_member_required
@require_http_methods(['POST'])
def reject_ad(request):
    """
    Reject ad: selected reason (required) and optional comment for "Other".
    Stored in rejection_reason; admin action logged. Validation ensures reason is chosen.
    """
    try:
        body = get_request_payload(request)
        ad_id = body.get('ad_id')
        reason = (body.get('reason') or '').strip()
        comment = (body.get('comment') or '').strip()
        if not ad_id:
            return JsonResponse({'status': 'error', 'message': 'Missing ad_id'}, status=400)
        if not reason:
            return JsonResponse({'status': 'error', 'message': 'Rejection reason is required'}, status=400)
        # Map dropdown value (key or label) to stored reason; "Other" allows optional comment
        labels_by_key = dict(REJECTION_REASONS_DETAIL)
        keys = list(labels_by_key.keys())
        if reason in keys:
            reason_key = reason
        else:
            reason_key = next((k for k, v in REJECTION_REASONS_DETAIL if v == reason), reason)
        stored_reason = labels_by_key.get(reason_key, reason)
        if reason_key == 'other' and comment:
            stored_reason = f"Other: {comment}"
        ad = get_object_or_404(AdRequest, uuid=ad_id)
        if ad.status not in (AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL):
            return JsonResponse({'status': 'error', 'message': 'Ad not in pending state'}, status=400)
        reject_one_ad(ad, stored_reason, rejected_by=request.user)
        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@staff_member_required
@require_http_methods(['POST'])
def bulk_approve(request):
    """Bulk approve: ad_ids list in JSON or form. Returns { status, approved_count }. """
    try:
        body = get_request_payload(request)
        ad_ids = body.get('ad_ids') if isinstance(body.get('ad_ids'), list) else request.POST.getlist('ad_ids') or []
        if not ad_ids:
            return JsonResponse({'status': 'error', 'message': 'No ad_ids provided'}, status=400)
        approved_count = 0
        for ad_id in ad_ids[:50]:
            try:
                ad = AdRequest.objects.get(uuid=ad_id)
                if ad.status not in (AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL):
                    continue
                approve_one_ad(ad, approved_by=request.user)
                approved_count += 1
            except AdRequest.DoesNotExist:
                pass
        return JsonResponse({'status': 'success', 'approved_count': approved_count})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


@staff_member_required
@require_http_methods(['POST'])
def bulk_reject(request):
    """Bulk reject: ad_ids + single reason. Returns { status, rejected_count }. """
    try:
        body = get_request_payload(request)
        ad_ids = body.get('ad_ids') if isinstance(body.get('ad_ids'), list) else request.POST.getlist('ad_ids') or []
        reason = (body.get('reason') or '').strip()
        if not ad_ids:
            return JsonResponse({'status': 'error', 'message': 'No ad_ids provided'}, status=400)
        if not reason:
            return JsonResponse({'status': 'error', 'message': 'Rejection reason is required'}, status=400)
        rejected_count = 0
        for ad_id in ad_ids[:50]:
            try:
                ad = AdRequest.objects.get(uuid=ad_id)
                if ad.status not in (AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL):
                    continue
                reject_one_ad(ad, reason, rejected_by=request.user)
                rejected_count += 1
            except AdRequest.DoesNotExist:
                pass
        return JsonResponse({'status': 'success', 'rejected_count': rejected_count})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=500)


def _bulk_reject_ads(ad_ids, reason, rejected_by):
    """Shared logic: reject up to 50 ads with one reason. Returns rejected_count."""
    rejected_count = 0
    for ad_id in ad_ids[:50]:
        try:
            ad = AdRequest.objects.get(uuid=ad_id)
            if ad.status not in (AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL):
                continue
            reject_one_ad(ad, reason, rejected_by=rejected_by)
            rejected_count += 1
        except AdRequest.DoesNotExist:
            pass
    return rejected_count


@staff_member_required
@require_http_methods(['GET', 'POST'])
def bulk_reject_page(request):
    """Standalone page: bulk reject with reason. GET: form with ids in query; POST: reject and redirect to ad list."""
    ids_param = request.GET.get('ids', '') if request.method == 'GET' else (request.POST.get('ids') or '')
    ad_ids = [x.strip() for x in ids_param.split(',') if x.strip()]
    # Validate UUIDs and only keep actionable ads for display
    ads_to_show = []
    valid_ids = []
    for uid in ad_ids[:50]:
        try:
            ad = AdRequest.objects.get(uuid=uid)
            if ad.status in (AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL):
                valid_ids.append(uid)
                ads_to_show.append(ad)
        except (AdRequest.DoesNotExist, ValueError):
            pass

    if request.method == 'POST':
        reason = (request.POST.get('reason') or '').strip()
        post_ids = request.POST.getlist('ad_ids') or [x.strip() for x in (request.POST.get('ids') or '').split(',') if x.strip()]
        if not post_ids:
            messages.error(request, 'No ads selected.')
            return redirect('ad_list')
        if not reason:
            messages.error(request, 'Rejection reason is required.')
            context = {
                'ad_ids': post_ids,
                'ads': AdRequest.objects.filter(uuid__in=post_ids[:50]),
                'reason': reason,
                'rejection_reasons': REJECTION_REASONS,
            }
            return render(request, 'core/bulk_reject.html', context)
        rejected_count = _bulk_reject_ads(post_ids, reason, rejected_by=request.user)
        messages.success(request, f'Rejected {rejected_count} ad(s).')
        return redirect('ad_list')

    if not valid_ids:
        messages.warning(request, 'No pending ads selected. Select ads from the Requests list first.')
        return redirect('ad_list')

    context = {
        'ad_ids': valid_ids,
        'ads': ads_to_show,
        'rejection_reasons': REJECTION_REASONS,
    }
    return render(request, 'core/bulk_reject.html', context)


# ---------- Category Management ----------

@staff_member_required
def category_list(request):
    """List all categories with add/edit/delete/toggle actions."""
    categories = Category.objects.all().order_by('order', 'name')
    return render(request, 'core/category_list.html', {'categories': categories})


@staff_member_required
def category_create(request):
    """Add a new category. GET: form page; POST: validate, save, redirect with success message."""
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        slug = (request.POST.get('slug') or '').strip()
        color = (request.POST.get('color') or '#7C4DFF').strip()
        icon = (request.POST.get('icon') or '').strip()
        is_active = request.POST.get('is_active') == 'on'
        try:
            order = int(request.POST.get('order') or 0)
        except (ValueError, TypeError):
            order = 0
        if not name:
            return render(request, 'core/category_form.html', {'category': None, 'error': 'Name is required'})
        if not slug:
            from django.utils.text import slugify
            slug = slugify(name)[:64]
        if Category.objects.filter(slug=slug).exists():
            return render(request, 'core/category_form.html', {'category': None, 'error': 'Slug already exists'})
        Category.objects.create(
            name=name, slug=slug, color=color or '#7C4DFF',
            icon=icon, is_active=is_active, order=order,
        )
        messages.success(request, 'Category added successfully!')
        return redirect('categories')
    return render(request, 'core/category_form.html', {'category': None})


@staff_member_required
def category_edit(request, pk):
    """Edit existing category. GET: form; POST: save."""
    category = get_object_or_404(Category, pk=pk)
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        slug = (request.POST.get('slug') or '').strip()
        color = (request.POST.get('color') or '#7C4DFF').strip()
        icon = (request.POST.get('icon') or '').strip()
        is_active = request.POST.get('is_active') == 'on'
        try:
            order = int(request.POST.get('order') or 0)
        except (ValueError, TypeError):
            order = 0
        if not name:
            return render(request, 'core/category_form.html', {'category': category, 'error': 'Name is required'})
        if not slug:
            from django.utils.text import slugify
            slug = slugify(name)[:64]
        if Category.objects.filter(slug=slug).exclude(pk=pk).exists():
            return render(request, 'core/category_form.html', {'category': category, 'error': 'Slug already exists'})
        category.name = name
        category.slug = slug
        category.color = color or '#7C4DFF'
        category.icon = icon
        category.is_active = is_active
        category.order = order
        category.save()
        messages.success(request, 'Category updated successfully!')
        return redirect('categories')
    return render(request, 'core/category_form.html', {'category': category})


@staff_member_required
@require_http_methods(['POST'])
def category_toggle(request, pk):
    """Toggle is_active via AJAX."""
    category = get_object_or_404(Category, pk=pk)
    category.is_active = not category.is_active
    category.save()
    return JsonResponse({'status': 'success', 'is_active': category.is_active})


@staff_member_required
@require_http_methods(['POST'])
def category_delete(request, pk):
    """Delete category. Ads with this category get category set to NULL; use SET_NULL or migrate to Other."""
    category = get_object_or_404(Category, pk=pk)
    other = Category.objects.filter(slug='other').first()
    if other:
        AdRequest.objects.filter(category=category).update(category=other)
    else:
        AdRequest.objects.filter(category=category).update(category=None)
    category.delete()
    return JsonResponse({'status': 'success', 'redirect': reverse('categories')})


# ---------- Admin Management (Superuser only) ----------

def _superuser_required(view_func):
    """Decorator: require authenticated superuser; return 403 otherwise."""
    from functools import wraps
    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated or not request.user.is_superuser:
            return HttpResponseForbidden("Superuser access required.")
        return view_func(request, *args, **kwargs)
    return _wrapped


@staff_member_required
@_superuser_required
@require_http_methods(['GET'])
def admin_management_list(request):
    """List all staff admins (AdminProfile) with notification status."""
    admins = AdminProfile.objects.select_related('user').order_by('user__username')
    return render(request, 'core/admin_management_list.html', {'admins': admins})


@staff_member_required
@_superuser_required
@require_http_methods(['GET', 'POST'])
def admin_management_create(request):
    """Create a new Django User and AdminProfile (username, password, telegram_id, nickname)."""
    User = get_user_model()
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        password = request.POST.get('password') or ''
        telegram_id_raw = (request.POST.get('telegram_id') or '').strip()
        telegram_id = "".join(c for c in telegram_id_raw if c.isdigit())
        admin_nickname = (request.POST.get('admin_nickname') or '').strip()[:64]
        is_notified = request.POST.get('is_notified') == 'on'
        if not username:
            return render(request, 'core/admin_management_form.html', {
                'is_create': True,
                'error': 'Username is required.',
            })
        if not password:
            return render(request, 'core/admin_management_form.html', {
                'is_create': True,
                'error': 'Password is required.',
            })
        if User.objects.filter(username__iexact=username).exists():
            return render(request, 'core/admin_management_form.html', {
                'is_create': True,
                'error': 'A user with that username already exists.',
            })
        user = User.objects.create_user(username=username, password=password)
        user.is_staff = True
        user.save(update_fields=['is_staff'])
        profile = AdminProfile.objects.create(
            user=user,
            telegram_id=telegram_id,
            is_notified=is_notified,
            admin_nickname=admin_nickname,
        )
        messages.success(request, f'Admin "{username}" created successfully.')
        return redirect('admin_management_list')
    return render(request, 'core/admin_management_form.html', {'is_create': True})


@staff_member_required
@_superuser_required
@require_http_methods(['GET', 'POST'])
def admin_management_edit(request, pk):
    """Edit AdminProfile: telegram_id, is_notified, admin_nickname. Optionally set new password."""
    profile = get_object_or_404(AdminProfile, pk=pk)
    User = get_user_model()
    if request.method == 'POST':
        telegram_id_raw = (request.POST.get('telegram_id') or '').strip()
        telegram_id = "".join(c for c in telegram_id_raw if c.isdigit())
        admin_nickname = (request.POST.get('admin_nickname') or '').strip()[:64]
        is_notified = request.POST.get('is_notified') == 'on'
        profile.telegram_id = telegram_id
        profile.admin_nickname = admin_nickname
        profile.is_notified = is_notified
        profile.save()
        new_password = request.POST.get('new_password') or ''
        if new_password:
            profile.user.set_password(new_password)
            profile.user.save(update_fields=['password'])
        messages.success(request, f'Admin "{profile.user.username}" updated successfully.')
        return redirect('admin_management_list')
    return render(request, 'core/admin_management_form.html', {
        'is_create': False,
        'profile': profile,
    })


@staff_member_required
@_superuser_required
@require_http_methods(['POST'])
def admin_test_notification(request, pk):
    """Send a test 'Ping' message to the admin's Telegram to verify telegram_id. Returns JSON with exact error on failure."""
    profile = get_object_or_404(AdminProfile, pk=pk)
    tid = (profile.telegram_id or "").strip()
    if not tid:
        return JsonResponse({"success": False, "message": "No Telegram ID set for this admin."}, status=400)
    from django.conf import settings
    from core.models import TelegramBot
    from core.bot_handler import send_message_to_chat
    env = getattr(settings, "ENVIRONMENT", "PROD")
    default_bot = (
        TelegramBot.objects.filter(environment=env, is_active=True)
        .order_by("-is_default")
        .first()
    )
    bot_mention = f"@{default_bot.username}" if default_bot and (default_bot.username or "").strip() else "the bot"
    test_text = (
        "🔔 Ping — اعلان تست از پنل مدیریت. اگر این پیام را می‌بینید، شناسه تلگرام درست است.\n\n"
        f"اگر اعلان دریافت نکردید، حتماً قبلاً ربات را با /start شروع کرده باشید ({bot_mention})."
    )
    success, err = send_message_to_chat(tid, test_text)
    if success:
        return JsonResponse({"success": True, "message": "Test notification sent."})
    return JsonResponse({"success": False, "message": err or "Send failed."}, status=502)


@staff_member_required
@require_http_methods(['GET'])
def export_config(request):
    """Export site configuration as JSON (no secrets in plain text)."""
    config = SiteConfiguration.get_config()
    data = {
        'is_ai_enabled': config.is_ai_enabled,
        'openai_model': config.openai_model,
        'ai_system_prompt': config.ai_system_prompt,
        'use_webhook': config.use_webhook,
        'telegram_bot_username': config.telegram_bot_username,
        'telegram_webhook_url': config.telegram_webhook_url,
        'approval_message_template': config.approval_message_template,
        'rejection_message_template': config.rejection_message_template,
        'submission_ack_message': config.submission_ack_message,
    }
    return JsonResponse(data, json_dumps_params={'indent': 2})


@csrf_exempt
@require_http_methods(['POST'])
def submit_ad(request):
    """
    Ingress API: create ad request (Telegram bot or external).
    If AI is enabled, runs moderation and sets pending_manual + ai_suggested_reason on reject.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    try:
        data = get_request_payload(request)
        content = (data.get('content') or '').strip()
        if not content:
            return JsonResponse({'error': 'content is required'}, status=400)
        content = clean_ad_text(content)
        slug = (data.get('category') or 'other').strip()
        category = Category.objects.filter(slug=slug, is_active=True).first() or Category.objects.filter(slug='other').first()
        if not category:
            category = Category.objects.order_by('order').first()
        config = SiteConfiguration.get_config()
        ad = AdRequest.objects.create(
            category=category,
            content=content,
            status=AdRequest.Status.PENDING_AI,
            telegram_user_id=data.get('telegram_user_id') or None,
            telegram_username=(data.get('telegram_username') or '')[:128],
            raw_telegram_json=data.get('raw_telegram_json'),
        )
        if config.is_ai_enabled:
            approved, reason = run_ai_moderation(ad.content, config)
            ad.status = AdRequest.Status.PENDING_MANUAL
            if not approved and reason:
                ad.ai_suggested_reason = reason[:500]
            ad.save()
        else:
            ad.status = AdRequest.Status.PENDING_MANUAL
            ad.save(update_fields=['status'])
        ack = (config.submission_ack_message or 'Your broadcast is currently under AI scrutiny. We\'ll notify you the moment it goes live.').strip()
        return JsonResponse({'status': 'created', 'uuid': str(ad.uuid), 'ack_message': ack})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@staff_member_required
@require_http_methods(['POST'])
def import_config(request):
    """Import configuration from JSON or form (merge non-secret fields)."""
    try:
        data = get_request_payload(request)
        if not data:
            return JsonResponse({'status': 'error', 'message': 'Invalid JSON'}, status=400)
        config = SiteConfiguration.get_config()
        if 'is_ai_enabled' in data:
            config.is_ai_enabled = bool(data['is_ai_enabled'])
        if 'openai_model' in data:
            config.openai_model = str(data['openai_model'])[:64]
        if 'ai_system_prompt' in data:
            config.ai_system_prompt = str(data['ai_system_prompt'])
        if 'use_webhook' in data:
            config.use_webhook = bool(data['use_webhook'])
        if 'telegram_webhook_url' in data:
            config.telegram_webhook_url = str(data['telegram_webhook_url'])
        if 'approval_message_template' in data:
            config.approval_message_template = str(data['approval_message_template'])
        if 'rejection_message_template' in data:
            config.rejection_message_template = str(data['rejection_message_template'])
        if 'submission_ack_message' in data:
            config.submission_ack_message = str(data['submission_ack_message'])
        if 'telegram_bot_username' in data:
            config.telegram_bot_username = str(data['telegram_bot_username'])[:64]
        config.save()
        return JsonResponse({'status': 'success'})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)}, status=400)


# ---------- Bot management (PART 2) ----------

@staff_member_required
@require_http_methods(['GET'])
def bot_list(request):
    """List all bots. Default bot for current environment in System Core card; others in table."""
    from django.conf import settings
    env = getattr(settings, "ENVIRONMENT", "PROD")
    default_bot = (
        TelegramBot.objects.filter(environment=env).order_by("-is_default").first()
    )
    other_bots = (
        TelegramBot.objects.exclude(pk=default_bot.pk).order_by("name")
        if default_bot
        else TelegramBot.objects.order_by("name")
    )
    default_bot_pulse_state = None
    default_bot_pulse_label = None
    if default_bot:
        from core.services.bot_manager import webhook_pulse_for_bot
        default_bot_pulse_state, default_bot_pulse_label = webhook_pulse_for_bot(default_bot)
    config = SiteConfiguration.get_config()
    production_base_url = (config.production_base_url or "").strip()
    production_base_url_set = bool(production_base_url and production_base_url.startswith("https://"))
    context = {
        'default_bot': default_bot,
        'default_bot_pulse_state': default_bot_pulse_state,
        'default_bot_pulse_label': default_bot_pulse_label,
        'other_bots': other_bots,
        'production_base_url_set': production_base_url_set,
        'production_base_url': production_base_url,
    }
    return render(request, 'core/bot_list.html', context)


@staff_member_required
@require_http_methods(['GET', 'POST'])
def bot_create(request):
    """Add new bot. POST: save name, token (encrypted), username, is_active, webhook_url."""
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        token = (request.POST.get('bot_token') or '').strip()
        if not name:
            return JsonResponse({'status': 'error', 'message': 'Name is required'}, status=400)
        if not token:
            return JsonResponse({'status': 'error', 'message': 'Bot token is required'}, status=400)
        bot = TelegramBot(name=name, username='', status=TelegramBot.Status.OFFLINE)
        bot.set_token(token)
        bot.username = (request.POST.get('username') or '').strip().lstrip('@')[:64]
        bot.is_active = request.POST.get('is_active') == 'on'
        bot.mode = (request.POST.get('mode') or TelegramBot.Mode.POLLING).strip() or TelegramBot.Mode.POLLING
        if bot.mode not in dict(TelegramBot.Mode.choices):
            bot.mode = TelegramBot.Mode.POLLING
        bot.webhook_url = (request.POST.get('webhook_url') or '').strip()
        bot.webhook_secret = (request.POST.get('webhook_secret') or '').strip()[:64]
        ok, msg = test_telegram_connection(bot.get_decrypted_token())
        if ok:
            bot.status = TelegramBot.Status.ONLINE
            bot.last_heartbeat = timezone.now()
        else:
            bot.status = TelegramBot.Status.ERROR
        bot.save()
        # Register webhook with Telegram so updates are delivered
        if bot.webhook_url:
            set_ok, set_msg = set_webhook(
                bot.get_decrypted_token(),
                bot.webhook_url,
                secret_token=bot.webhook_secret or None,
            )
            if not set_ok:
                logger.warning("Bot create: set_webhook failed for bot_id=%s: %s", bot.pk, set_msg)
        return JsonResponse({'status': 'success', 'redirect': reverse('bot_edit', kwargs={'pk': bot.pk})})
    return render(request, 'core/bot_form.html', {'bot': None, 'is_create': True})


@staff_member_required
@require_http_methods(['GET', 'POST'])
def bot_edit(request, pk):
    """Edit bot. Token from form or JSON (get_request_payload avoids RawPostDataException). Validate with getMe; sync webhook or clear for polling."""
    bot = get_object_or_404(TelegramBot, pk=pk)
    if request.method == 'POST':
        data = get_request_payload(request)
        bot.name = (data.get('name') or bot.name or '').strip()
        new_token = (data.get('bot_token') or '').strip()
        if new_token and new_token != bot.get_masked_token():
            from core.bot_handler import validate_token
            ok, err, _ = validate_token(new_token)
            if not ok:
                return JsonResponse({'status': 'error', 'message': err or 'Invalid token'}, status=400)
            bot.set_token(new_token)
        bot.username = (data.get('username') or '').strip().lstrip('@')[:64]
        bot.is_active = data.get('is_active') == 'on' or data.get('is_active') is True
        mode = (data.get('mode') or bot.mode or TelegramBot.Mode.POLLING).strip()
        if mode in dict(TelegramBot.Mode.choices):
            bot.mode = mode
        bot.webhook_url = (data.get('webhook_url') or '').strip()
        bot.webhook_secret = (data.get('webhook_secret') or '').strip()[:64]
        bot.save()
        token = bot.get_decrypted_token()
        if bot.mode == TelegramBot.Mode.WEBHOOK and (bot.webhook_url or bot.is_default):
            if bot.is_default:
                from core.services.bot_manager import activate_webhook
                set_ok, set_msg, _ = activate_webhook(bot)
                if not set_ok:
                    logger.warning("Bot edit: activate_webhook failed for bot_id=%s: %s", bot.pk, set_msg)
            elif bot.webhook_url and token:
                set_ok, set_msg = set_webhook(token, bot.webhook_url, secret_token=bot.webhook_secret or None)
                if not set_ok:
                    logger.warning("Bot edit: set_webhook failed for bot_id=%s: %s", bot.pk, set_msg)
        else:
            if token:
                delete_webhook(token, drop_pending_updates=True)
        return JsonResponse({'status': 'success'})
    context = {'bot': bot, 'is_create': False}
    return render(request, 'core/bot_form.html', context)


@staff_member_required
@require_http_methods(['GET', 'POST'])
def bot_delete(request, pk):
    """Confirm and delete bot."""
    bot = get_object_or_404(TelegramBot, pk=pk)
    if request.method == 'POST':
        bot.delete()
        return JsonResponse({'status': 'success', 'redirect': reverse('bot_list')})
    return render(request, 'core/bot_confirm_delete.html', {'bot': bot})


@staff_member_required
@require_http_methods(['POST'])
def bot_test(request, pk):
    """Test bot connection (getMe). Returns JSON { success, message }."""
    bot = get_object_or_404(TelegramBot, pk=pk)
    token = bot.get_decrypted_token()
    ok, msg = test_telegram_connection(token)
    if ok:
        bot.status = TelegramBot.Status.ONLINE
        bot.last_heartbeat = timezone.now()
        bot.last_error = ''
        bot.save(update_fields=['status', 'last_heartbeat', 'last_error'])
    return JsonResponse({'success': ok, 'message': msg})


@staff_member_required
@require_http_methods(['POST'])
def bot_sync_webhook(request, pk):
    """Sync webhook for default bot: deleteWebhook then setWebhook with production_base_url. POST only."""
    bot = get_object_or_404(TelegramBot, pk=pk)
    if not bot.is_default:
        return JsonResponse({'success': False, 'message': 'Only the default bot can use this endpoint.'}, status=400)
    from core.services.bot_manager import activate_webhook
    success, message, url = activate_webhook(bot)
    return JsonResponse({'success': success, 'message': message or ('Webhook synced.' if success else 'Webhook sync failed.'), 'url': url})


@staff_member_required
@require_http_methods(['POST'])
def bot_reset_connection(request, pk):
    """Troubleshooting: clear webhook (drop_pending_updates), then re-sync if Webhook mode or leave ready for Polling."""
    bot = get_object_or_404(TelegramBot, pk=pk)
    token = bot.get_decrypted_token()
    if not token:
        return JsonResponse({'success': False, 'message': 'No token configured.'}, status=400)
    ok_clear, err_clear = delete_webhook(token, drop_pending_updates=True)
    if not ok_clear:
        return JsonResponse({'success': False, 'message': f'Clear webhook failed: {err_clear or "Unknown"}'}, status=400)
    if bot.mode == TelegramBot.Mode.WEBHOOK:
        from core.services.bot_manager import activate_webhook
        success, message, url = activate_webhook(bot)
        if success:
            return JsonResponse({'success': True, 'message': f'Connection reset. {message}', 'url': url})
        return JsonResponse({'success': False, 'message': f'Webhook re-sync failed: {message or "Unknown"}'}, status=400)
    return JsonResponse({'success': True, 'message': 'Connection reset. Webhook cleared; bot is ready for polling (run python manage.py runbots).'})


@staff_member_required
@require_http_methods(['POST'])
def bot_update_default_token(request, pk):
    """Update default bot token. Validates with getMe; if Webhook mode syncs webhook, if Polling clears webhook. POST: bot_token (form or JSON via get_request_payload)."""
    bot = get_object_or_404(TelegramBot, pk=pk)
    if not bot.is_default:
        return JsonResponse({'success': False, 'message': 'Only the default bot can use this endpoint.'}, status=400)
    data = get_request_payload(request)
    new_token = (data.get('bot_token') or '').strip()
    if not new_token:
        return JsonResponse({'success': False, 'message': 'Token is required.'}, status=400)
    bot.set_token(new_token)
    bot.save(update_fields=['bot_token_encrypted'])
    token = bot.get_decrypted_token()
    ok, msg = test_telegram_connection(token)
    if ok:
        bot.status = TelegramBot.Status.ONLINE
        bot.last_heartbeat = timezone.now()
        bot.last_error = ''
        bot.save(update_fields=['status', 'last_heartbeat', 'last_error'])
        if bot.mode == TelegramBot.Mode.WEBHOOK:
            from core.services.bot_manager import activate_webhook
            success, webhook_msg, url = activate_webhook(bot)
            if success:
                msg = f"Token updated. {webhook_msg}"
            elif webhook_msg and 'production_base_url' in (webhook_msg or '').lower():
                msg = "Token updated. Set production_base_url in Settings to activate webhook."
            else:
                msg = f"Token updated. Webhook: {webhook_msg or 'not set'}"
        else:
            ok_del, err_del = delete_webhook(token, drop_pending_updates=True)
            msg = "Token updated. Webhook cleared for polling." if ok_del else f"Token updated. Webhook clear: {err_del or 'ok'}"
    else:
        bot.status = TelegramBot.Status.ERROR
        bot.last_error = msg or 'Invalid token'
        bot.save(update_fields=['status', 'last_error'])
        return JsonResponse({'success': False, 'message': msg or 'Invalid token'}, status=400)
    return JsonResponse({'success': True, 'message': msg})


@staff_member_required
@require_http_methods(['POST'])
def bot_regenerate_webhook(request, pk):
    """Set or clear webhook for bot. POST: webhook_url (optional). If empty, delete webhook."""
    bot = get_object_or_404(TelegramBot, pk=pk)
    data = get_request_payload(request)
    url = (data.get('webhook_url') or '').strip()
    token = bot.get_decrypted_token()
    if not url:
        ok, msg = delete_webhook(token)
    else:
        ok, msg = set_webhook(token, url, secret_token=bot.webhook_secret or None)
    if ok and url:
        bot.webhook_url = url
        bot.save(update_fields=['webhook_url'])
    return JsonResponse({'success': ok, 'message': msg})


@staff_member_required
@require_http_methods(['POST'])
def bot_start(request, pk):
    """Request start of polling worker. runbots process will apply it."""
    bot = get_object_or_404(TelegramBot, pk=pk)
    if bot.mode != TelegramBot.Mode.POLLING:
        return JsonResponse({'status': 'error', 'message': 'Only polling bots can be started'}, status=400)
    bot.requested_action = TelegramBot.RequestedAction.START
    bot.save(update_fields=['requested_action'])
    return JsonResponse({'status': 'success', 'message': 'Start requested. Run python manage.py runbots if not running.'})


@staff_member_required
@require_http_methods(['POST'])
def bot_stop(request, pk):
    """Request stop of polling worker."""
    bot = get_object_or_404(TelegramBot, pk=pk)
    if bot.mode != TelegramBot.Mode.POLLING:
        return JsonResponse({'status': 'error', 'message': 'Only polling bots can be stopped'}, status=400)
    bot.requested_action = TelegramBot.RequestedAction.STOP
    bot.save(update_fields=['requested_action'])
    return JsonResponse({'status': 'success', 'message': 'Stop requested.'})


@staff_member_required
@require_http_methods(['POST'])
def bot_restart(request, pk):
    """Request restart of polling worker."""
    bot = get_object_or_404(TelegramBot, pk=pk)
    if bot.mode != TelegramBot.Mode.POLLING:
        return JsonResponse({'status': 'error', 'message': 'Only polling bots can be restarted'}, status=400)
    bot.requested_action = TelegramBot.RequestedAction.RESTART
    bot.save(update_fields=['requested_action'])
    return JsonResponse({'status': 'success', 'message': 'Restart requested.'})


@staff_member_required
@require_http_methods(['POST'])
def toggle_bot_status(request, bot_id):
    """
    Start or stop the runbots process for this bot.
    Start: spawns `TELEGRAM_MODE=polling python manage.py runbots --bot-id=ID` and stores PID.
    Stop: sends SIGTERM to the process stored in current_pid and clears current_pid / is_running.
    """
    bot = get_object_or_404(TelegramBot, pk=bot_id)
    if bot.mode != TelegramBot.Mode.POLLING:
        messages.error(request, "Only polling bots can be started or stopped from this panel.")
        return redirect("bot_list")

    if getattr(bot, "is_running", False) and getattr(bot, "current_pid", None):
        # Stop: kill the runbots process
        pid = bot.current_pid
        try:
            os.kill(pid, signal.SIGTERM)
        except (OSError, ProcessLookupError, AttributeError) as e:
            logger.warning("toggle_bot_status stop: kill pid=%s failed: %s", pid, e)
        bot.current_pid = None
        bot.is_running = False
        bot.save(update_fields=["current_pid", "is_running"])
        messages.success(request, f"Bot «{bot.name}» runbots process (PID {pid}) stopped.")
        return redirect("bot_list")

    # Start: run manage.py runbots --bot-id=bot_id in background
    base_dir = Path(getattr(settings, "BASE_DIR", ".")).resolve()
    manage_py = base_dir / "manage.py"
    if not manage_py.exists():
        messages.error(request, "manage.py not found. Cannot start runbots.")
        return redirect("bot_list")
    env = os.environ.copy()
    env["TELEGRAM_MODE"] = "polling"
    cmd = [sys.executable, str(manage_py), "runbots", "--bot-id", str(bot_id)]
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(base_dir),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        bot.current_pid = proc.pid
        bot.is_running = True
        bot.save(update_fields=["current_pid", "is_running"])
        messages.success(request, f"Bot «{bot.name}» runbots started (PID {proc.pid}).")
    except Exception as e:
        logger.exception("toggle_bot_status start: %s", e)
        messages.error(request, f"Failed to start runbots: {e}")
    return redirect("bot_list")


# ---------- Instagram settings ----------

@staff_member_required
@require_http_methods(['GET'])
def settings_instagram(request):
    """List Instagram configurations; link to add/edit."""
    configs = InstagramConfiguration.objects.all().order_by('username')
    return render(request, 'core/settings_instagram.html', {'configs': configs})


@staff_member_required
@require_http_methods(['GET', 'POST'])
def settings_instagram_edit(request, pk=None):
    """Create or edit Instagram config. Token encrypted; test connection."""
    from core.services.instagram import InstagramService

    config = get_object_or_404(InstagramConfiguration, pk=pk) if pk else None
    if request.method == 'POST':
        username = (request.POST.get('username') or '').strip()
        if not username:
            return JsonResponse({'status': 'error', 'message': 'Username is required'}, status=400)
        token = (request.POST.get('access_token') or '').strip()
        if not config and not token:
            return JsonResponse({'status': 'error', 'message': 'Access token is required for new config'}, status=400)
        if not config:
            config = InstagramConfiguration(username=username)
        else:
            config.username = username
        if token and token != (getattr(config, '_masked', '') or '••••••••'):
            config.set_access_token(token)
        config.page_id = (request.POST.get('page_id') or '').strip()[:64]
        config.ig_user_id = (request.POST.get('ig_user_id') or '').strip()[:64]
        config.placeholder_image_url = (request.POST.get('placeholder_image_url') or '').strip()
        config.is_active = request.POST.get('is_active') == 'on'
        config.save()
        return JsonResponse({'status': 'success', 'redirect': reverse('settings_instagram')})
    return render(request, 'core/settings_instagram_form.html', {'config': config, 'is_create': config is None})


@staff_member_required
@require_http_methods(['POST'])
def settings_instagram_test(request, pk):
    """Test Instagram credentials. Returns JSON { success, message }."""
    from core.services.instagram import InstagramService

    config = get_object_or_404(InstagramConfiguration, pk=pk)
    ok, msg = InstagramService.validate_credentials(config)
    return JsonResponse({'success': ok, 'message': msg})


@staff_member_required
@require_http_methods(['POST'])
@csrf_exempt
def api_instagram_post(request):
    """
    POST /api/instagram/post/
    Body: { image_url, caption } or { image_url, message_text, email, phone }
    Optional: scheduled_at (ISO) — schedules instead of posting immediately.
    Returns JSON { success, message, id } or { scheduled: true, pk }.
    """
    from core.services.instagram import InstagramService

    data = get_request_payload(request)
    image_url = (data.get('image_url') or '').strip()
    caption = (data.get('caption') or '').strip()
    message_text = (data.get('message_text') or '').strip()
    email = (data.get('email') or '').strip()[:254]
    phone = (data.get('phone') or '').strip()[:20]
    scheduled_at_str = (data.get('scheduled_at') or '').strip()
    lang = (data.get('lang') or 'en').strip() or 'en'

    if not image_url:
        return JsonResponse({'success': False, 'message': 'image_url is required'}, status=400)

    if not caption and message_text:
        parts = [message_text]
        if email:
            parts.append(f'📧 {email}')
        if phone:
            parts.append(f'📞 {phone}')
        parts.append('🙏 Iraniu — trusted classifieds' if lang == 'en' else '🙏 ایرانيو — آگهی‌های معتبر')
        caption = '\n\n'.join(parts)[:2200]

    if not caption:
        return JsonResponse({'success': False, 'message': 'caption or message_text is required'}, status=400)

    if scheduled_at_str:
        try:
            from datetime import datetime
            scheduled_at = datetime.fromisoformat(scheduled_at_str.replace('Z', '+00:00'))
            if scheduled_at.tzinfo is None:
                scheduled_at = timezone.make_aware(scheduled_at)
        except (ValueError, TypeError):
            return JsonResponse({'success': False, 'message': 'Invalid scheduled_at format (use ISO 8601)'}, status=400)
        post = ScheduledInstagramPost.objects.create(
            image_url=image_url,
            caption=caption,
            message_text=message_text,
            email=email,
            phone=phone,
            scheduled_at=scheduled_at,
        )
        return JsonResponse({'scheduled': True, 'pk': post.pk, 'scheduled_at': scheduled_at_str})

    result = InstagramService.post_custom(image_url=image_url, caption=caption)
    if result.get('success'):
        return JsonResponse({'success': True, 'message': result.get('message', 'Published'), 'id': result.get('id')})
    return JsonResponse({'success': False, 'message': result.get('message', 'Unknown error')}, status=500)


# ---------- API clients ----------

@staff_member_required
@require_http_methods(['GET'])
def settings_api(request):
    """List API clients; create, regenerate key, rate limit, revoke."""
    clients = ApiClient.objects.all().order_by('name')
    return render(request, 'core/settings_api.html', {'clients': clients})


@staff_member_required
@require_http_methods(['GET'])
def settings_api_key_display(request):
    """One-time display of new API key (after create or regenerate). Key stored in session; cleared after show."""
    new_key = request.session.pop('api_new_key', None)
    client_name = request.session.pop('api_new_key_client_name', None)
    if not new_key:
        messages.info(request, 'No new key to display. Create or regenerate an API client to see a key here.')
        return redirect('settings_api')
    context = {'new_key': new_key, 'client_name': client_name or 'API client'}
    return render(request, 'core/settings_api_key_display.html', context)


@staff_member_required
@require_http_methods(['GET', 'POST'])
def settings_api_edit(request, pk=None):
    """Create or edit API client. On create/regenerate, redirects to key-display page with key in session."""
    from core.encryption import hash_api_key
    import secrets

    client = get_object_or_404(ApiClient, pk=pk) if pk else None
    new_key_plain = None
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, 'Name is required.')
            return render(request, 'core/settings_api_form.html', {'client': client, 'is_create': client is None})
        if not client:
            new_key_plain = secrets.token_urlsafe(32)
            client = ApiClient(name=name, api_key_hashed=hash_api_key(new_key_plain))
        else:
            client.name = name
        client.rate_limit_per_min = max(1, min(1000, int(request.POST.get('rate_limit_per_min') or 60)))
        client.is_active = request.POST.get('is_active') == 'on'
        if request.POST.get('regenerate_key') == 'on' and client.pk:
            new_key_plain = secrets.token_urlsafe(32)
            client.api_key_hashed = hash_api_key(new_key_plain)
        client.save()
        if new_key_plain:
            request.session['api_new_key'] = new_key_plain
            request.session['api_new_key_client_name'] = client.name
            messages.success(request, 'API client saved. Copy the key below; it will not be shown again.')
            return redirect('settings_api_key_display')
        messages.success(request, 'API client saved.')
        return redirect('settings_api')
    return render(request, 'core/settings_api_form.html', {'client': client, 'is_create': client is None})


# ---------- Delivery log ----------

@staff_member_required
@require_http_methods(['GET'])
def delivery_list(request):
    """List delivery logs; filter by channel/status; retry failed."""
    qs = DeliveryLog.objects.select_related('ad').order_by('-created_at')
    channel = request.GET.get('channel', '').strip()
    status = request.GET.get('status', '').strip()
    if channel and channel in dict(DeliveryLog.Channel.choices):
        qs = qs.filter(channel=channel)
    if status and status in dict(DeliveryLog.DeliveryStatus.choices):
        qs = qs.filter(status=status)
    paginator = Paginator(qs, 25)
    page_obj = paginator.get_page(request.GET.get('page', 1))
    return render(request, 'core/delivery_list.html', {
        'page_obj': page_obj,
        'channel_choices': DeliveryLog.Channel.choices,
        'status_choices': DeliveryLog.DeliveryStatus.choices,
        'filters': {'channel': channel, 'status': status},
    })


@staff_member_required
@require_http_methods(['POST'])
def delivery_retry(request, pk):
    """Retry delivery for a failed log. Returns JSON."""
    from core.services.delivery import DeliveryService

    log = get_object_or_404(DeliveryLog, pk=pk)
    if log.status != DeliveryLog.DeliveryStatus.FAILED:
        return JsonResponse({'status': 'error', 'message': 'Only failed deliveries can be retried'}, status=400)
    ok = DeliveryService.send(log.ad, log.channel)
    return JsonResponse({'status': 'success', 'delivery_ok': ok})


# --- Template Tester (Ad image generation) ---


def _save_temp_test_background(uploaded_file) -> str | None:
    """Save uploaded image to temp_test_backgrounds and return absolute path. Caller can store in session for Coordinate Lab."""
    if not uploaded_file:
        return None
    media_root = Path(getattr(settings, 'MEDIA_ROOT', None) or settings.BASE_DIR / 'media')
    temp_dir = media_root / 'temp_test_backgrounds'
    temp_dir.mkdir(parents=True, exist_ok=True)
    ext = Path(uploaded_file.name).suffix or '.png'
    if ext.lower() not in ('.png', '.jpg', '.jpeg', '.webp'):
        ext = '.png'
    name = f"{uuid.uuid4().hex}{ext}"
    path = temp_dir / name
    try:
        with open(path, 'wb') as f:
            for chunk in uploaded_file.chunks():
                f.write(chunk)
        return str(path.resolve())
    except Exception as e:
        logger.warning("Failed to save temp test background: %s", e)
        return None


@staff_member_required
@require_http_methods(['GET', 'POST'])
def template_create(request):
    """Create a new AdTemplate (name, background_image, font_file). Redirects to Coordinate Lab for the new template."""
    from django.urls import reverse
    form = AdTemplateCreateForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        template = form.save(commit=False)
        # coordinates use model default (default_adtemplate_coordinates)
        template.save()
        messages.success(request, f'Template "{template.name}" created. Position labels in the Coordinate Lab.')
        return redirect(reverse('admin:core_adtemplate_coordinate_lab', args=[template.pk]))
    return render(request, 'core/template_create.html', {'form': form})


@staff_member_required
@require_http_methods(['GET', 'POST'])
def template_tester(request):
    """Preview ad image: select template, dummy text, and optional custom background. POST generates with optional upload."""
    templates = AdTemplate.objects.filter(is_active=True).order_by('name')
    template = None
    preview_url = None
    validation_error = None
    used_custom_background = False
    form = TemplateTesterForm(templates=templates)

    if request.method == 'POST':
        form = TemplateTesterForm(request.POST, request.FILES, templates=templates)
        if form.is_valid():
            template_id = form.cleaned_data['template_id']
            category_text = form.cleaned_data['category_text']
            ad_text = form.cleaned_data['ad_text']
            phone_number = form.cleaned_data.get('phone_number') or '+98 912 345 6789'
            valid, err = validate_ad_content(ad_text)
            if not valid:
                validation_error = err
            else:
                template = get_object_or_404(AdTemplate, pk=template_id)
                background_file = None
                temp_bg_path = None
                uploaded = request.FILES.get('test_background')
                if uploaded:
                    temp_bg_path = _save_temp_test_background(uploaded)
                    if temp_bg_path:
                        background_file = temp_bg_path
                        used_custom_background = True
                        request.session[COORD_LAB_TEMP_BACKGROUND_KEY] = temp_bg_path

                from core.services.image_engine import create_ad_image
                path_str = create_ad_image(
                    int(template_id),
                    category_text,
                    ad_text,
                    phone_number,
                    background_file=background_file,
                )
                if path_str:
                    media_url = (getattr(settings, 'MEDIA_URL', '/media/') or '/media/').rstrip('/')
                    rel = Path(path_str).name
                    preview_url = f"{request.scheme}://{request.get_host()}{media_url}/generated_ads/{rel}"
                else:
                    validation_error = "Image generation failed. Check template background and coordinates."
        else:
            validation_error = "Please fix the form errors."
            if form.cleaned_data.get('template_id'):
                template = AdTemplate.objects.filter(pk=form.cleaned_data['template_id']).first()
    else:
        # GET: optional query params to prefill form and show preview via GET (no file)
        template_id = request.GET.get('template_id')
        if template_id:
            template = AdTemplate.objects.filter(pk=template_id).first()
            if template:
                form = TemplateTesterForm(
                    initial={
                        'template_id': template_id,
                        'category_text': request.GET.get('category_text', 'Category Heading'),
                        'ad_text': request.GET.get('ad_text', 'Sample ad text for preview. Change this in the form.'),
                        'phone_number': request.GET.get('phone_number', '+98 912 345 6789'),
                    },
                    templates=templates,
                )
                ad_text = request.GET.get('ad_text', '')
                valid, err = validate_ad_content(ad_text)
                if not valid:
                    validation_error = err
                else:
                    preview_url = (
                        reverse('template_tester_preview')
                        + f'?template_id={template.pk}'
                        + f'&category_text={quote(request.GET.get('category_text', 'Category Heading'))}'
                        + f'&ad_text={quote(request.GET.get('ad_text', 'Sample ad text for preview.'))}'
                        + f'&phone_number={quote(request.GET.get('phone_number', '+98 912 345 6789'))}'
                    )

    try:
        from core.services.post_manager import get_default_channel
        default_channel = get_default_channel()
    except Exception:
        default_channel = None

    context = {
        'form': form,
        'templates': templates,
        'template': template,
        'preview_url': preview_url,
        'default_channel': default_channel,
        'validation_error': validation_error,
        'used_custom_background': used_custom_background,
    }
    return render(request, 'core/template_tester.html', context)


@staff_member_required
@require_http_methods(['GET'])
def template_tester_preview(request):
    """Return generated ad image for template + dummy text (uses image_engine.create_ad_image)."""
    from core.services.image_engine import create_ad_image

    template_id = request.GET.get('template_id')
    if not template_id:
        return HttpResponse(status=400)
    template = get_object_or_404(AdTemplate, pk=template_id)
    category_text = request.GET.get('category_text', 'Category')
    ad_text = request.GET.get('ad_text', 'Ad text')
    phone_number = request.GET.get('phone_number', '')

    path_str = create_ad_image(int(template_id), category_text, ad_text, phone_number)
    if not path_str:
        return HttpResponse(status=500)
    with open(path_str, 'rb') as f:
        return HttpResponse(f.read(), content_type='image/png')


# ---------- Channel Manager (dashboard/channels/) ----------

@staff_member_required
@require_http_methods(['GET', 'POST'])
def channel_list(request):
    """
    List all Telegram channels and add new one. Staff/superuser only.
    POST: create channel; GET: list + form.
    """
    from django.conf import settings
    env = getattr(settings, "ENVIRONMENT", "PROD")
    channels = (
        TelegramChannel.objects.filter(bot_connection__environment=env)
        .select_related("bot_connection")
        .order_by("-is_default", "title")
    )
    form = ChannelForm()
    if request.method == "POST":
        form = ChannelForm(request.POST)
        if form.is_valid():
            channel = form.save(commit=False)
            channel.site_config_id = SiteConfiguration.get_config().pk
            channel.save()
            messages.success(request, f'Channel "{channel.title}" added successfully.')
            next_url = (request.POST.get('next') or request.GET.get('next') or '').strip()
            if next_url:
                return redirect(next_url)
            return redirect("channel_list")
        else:
            messages.error(request, "Please correct the errors below.")
    context = {
        "channels": channels,
        "form": form,
    }
    return render(request, "dashboard/channels.html", context)


@staff_member_required
@require_http_methods(['POST'])
def channel_delete(request, pk):
    """Delete a channel. Staff only. Redirects to channel_list with message."""
    channel = get_object_or_404(TelegramChannel, pk=pk)
    title = channel.title
    channel.delete()
    messages.success(request, f'Channel "{title}" has been deleted.')
    return redirect("channel_list")


@staff_member_required
@require_http_methods(['POST'])
def channel_set_default(request, pk):
    """Set this channel as the default (only one default at a time). Staff only."""
    channel = get_object_or_404(TelegramChannel, pk=pk)
    TelegramChannel.objects.filter(is_default=True).exclude(pk=pk).update(is_default=False)
    channel.is_default = True
    channel.save(update_fields=["is_default"])
    messages.success(request, f'"{channel.title}" is now the default channel.')
    return redirect("channel_list")


@staff_member_required
@require_http_methods(['POST'])
def channel_test_connection(request, pk):
    """Send a test message to the channel to verify bot admin rights. Returns JSON or redirect."""
    from core.services.telegram_client import send_message
    channel = get_object_or_404(TelegramChannel, pk=pk)
    try:
        token = channel.bot_connection.get_decrypted_token()
    except Exception:
        messages.error(request, "Could not get bot token.")
        return redirect("channel_list")
    if not token:
        messages.error(request, "Bot has no token configured.")
        return redirect("channel_list")
    try:
        chat_id = int(channel.channel_id.strip())
    except (ValueError, TypeError):
        messages.error(request, "Invalid channel ID.")
        return redirect("channel_list")
    text = "🔔 Test message from Iraniu Channel Manager — if you see this, the bot has admin rights."
    success, _, api_error = send_message(token, chat_id, text)
    if success:
        messages.success(request, f'Test message sent to "{channel.title}".')
    else:
        messages.error(request, api_error or "Failed to send test message. Check bot admin rights and channel ID.")
    return redirect("channel_list")
