"""
Iraniu — Staff-only views. Request/response only; business logic in services.
"""

import json
import logging
import os
import signal
import subprocess
import sys
import uuid
import csv
from urllib.parse import quote
from datetime import timedelta, timezone as dt_timezone
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.http import HttpResponse, JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError

from core.models import (
    AdRequest,
    AdTemplate,
    AdminProfile,
    Category,
    SiteConfiguration,
    SystemStatus,
    TelegramBot,
    TelegramChannel,
    TelegramUser,
    InstagramConfiguration,
    ApiClient,
    DeliveryLog,
    Notification,
    ScheduledInstagramPost,
    ActivityLog,
    REJECTION_REASONS,
    REJECTION_REASONS_DETAIL,
    default_adtemplate_coordinates,
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
from core.services.instagram import validate_instagram_token
from core.services.telegram_client import get_me as telegram_get_me
from core.services.dashboard import get_dashboard_context, get_pulse_data
from core.services.ad_actions import approve_one_ad, reject_one_ad, request_revision_one_ad
from core.services.activity_log import log_activity
from core.view_utils import get_request_payload
from core.forms import (
    AdTemplateCreateForm,
    ChannelForm,
    DesignDefaultsForm,
    InstagramBusinessForm,
    TelegramBotConfigForm,
    TemplateTesterForm,
)
from core.utils.validation import parse_hex_color, parse_int_in_range, validate_uploaded_image

logger = logging.getLogger(__name__)

# Session key for passing uploaded test background to Coordinate Lab
COORD_LAB_TEMP_BACKGROUND_KEY = "coord_lab_temp_background_path"


def _json_server_error(message: str, *, status: int = 500) -> JsonResponse:
    """Return a generic JSON error response without exposing internal exceptions."""
    return JsonResponse({'status': 'error', 'message': message}, status=status)


RESTRICTED_SETTINGS_KEYS = {
    'openai_api_key',
    'openai_model',
    'is_ai_enabled',
    'ai_system_prompt',
    'instagram_app_id',
    'instagram_app_secret',
    'instagram_business_id',
    'facebook_access_token',
    'production_base_url',
    'default_font',
    'default_watermark_opacity',
    'default_watermark',
    'default_primary_color',
    'default_secondary_color',
    'default_accent_color',
}


def _can_edit_restricted_settings(user) -> bool:
    return bool(user and user.is_authenticated and (user.is_superuser or user.has_perm('core.can_edit_settings')))


def _role_label(user) -> str:
    if user.is_superuser:
        return 'Admin'
    if user.has_perm('core.can_edit_settings'):
        return 'Editor'
    return 'Viewer'


def _available_font_options() -> list[tuple[str, str]]:
    """Return selectable font options from uploaded/template/static fonts."""
    options = [('Inter', 'Inter (Default)')]
    seen = {'inter'}

    # Uploaded template fonts
    for tpl in AdTemplate.objects.exclude(font_file='').only('name', 'font_file'):
        try:
            name = Path(str(tpl.font_file)).name
        except Exception:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        options.append((name, f'{name} (Template: {tpl.name})'))

    # Static fonts
    static_fonts_dir = Path(settings.BASE_DIR) / 'static' / 'fonts'
    if static_fonts_dir.exists():
        for ext in ('*.ttf', '*.otf', '*.woff2'):
            for font_file in sorted(static_fonts_dir.rglob(ext)):
                name = font_file.name
                key = name.lower()
                if key in seen:
                    continue
                seen.add(key)
                options.append((name, name))

    return options


def _sanitize_workflow_stages(raw_stages):
    """Normalize workflow stage payload to [{key,label,enabled}, ...]."""
    if not isinstance(raw_stages, list):
        return SiteConfiguration._meta.get_field('workflow_stages').default()
    cleaned = []
    used_keys = set()
    for idx, stage in enumerate(raw_stages[:12]):
        if not isinstance(stage, dict):
            continue
        label = (str(stage.get('label') or '')).strip()[:40]
        key = (str(stage.get('key') or '')).strip().lower()[:24]
        if not label:
            continue
        if not key:
            key = f'stage_{idx+1}'
        if key in used_keys:
            key = f'{key}_{idx+1}'
        used_keys.add(key)
        cleaned.append({'key': key, 'label': label, 'enabled': bool(stage.get('enabled', True))})
    return cleaned or SiteConfiguration._meta.get_field('workflow_stages').default()


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
    For approved ads, generates and displays the exact image that will be sent to Telegram.
    """
    ad = get_object_or_404(AdRequest, uuid=uuid)
    # Client: linked TelegramUser (ad.user) or lookup by telegram_user_id for read-only display
    client = None
    if getattr(ad, 'user_id', None) and ad.user_id:
        client = ad.user
    elif ad.telegram_user_id:
        client = TelegramUser.objects.filter(telegram_user_id=ad.telegram_user_id).first()

    preview_image_url = None
    if ad.status == AdRequest.Status.APPROVED:
        from core.services.image_engine import generate_ad_image
        from core.services.instagram_api import _path_to_public_url
        feed_path = generate_ad_image(ad, is_story=False)
        if feed_path:
            preview_image_url = _path_to_public_url(feed_path)

    context = {
        'ad': ad,
        'client': client,
        'ai_suggested_reason': ad.ai_suggested_reason or '',
        'preview_image_url': preview_image_url,
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

    # GET: build preview (use stored Feed image; Telegram caption = professional format from DeliveryService)
    from core.services.image_engine import ensure_feed_image
    from core.services.instagram_api import get_absolute_media_url
    from core.services.instagram import InstagramService
    from core.services.post_manager import get_default_channel
    from core.services.delivery import DeliveryService

    ensure_feed_image(ad)
    preview_image_url = get_absolute_media_url(ad.generated_image) if ad.generated_image else None

    caption_preview = InstagramService.format_caption(ad, lang='fa')
    telegram_preview = DeliveryService._build_channel_caption(ad)
    if len(telegram_preview) > 1024:
        telegram_preview = telegram_preview[:1021] + "…"

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
    # Guard: check if Instagram is enabled
    config = SiteConfiguration.get_config()
    if not getattr(config, 'is_instagram_enabled', False):
        return JsonResponse({'success': False, 'message': 'Instagram is not enabled. Complete Instagram settings first.'}, status=400)

    ad = get_object_or_404(AdRequest, uuid=uuid)
    if target not in ('feed', 'story'):
        return JsonResponse({'success': False, 'message': 'Invalid target'}, status=400)
    is_story = target == 'story'

    from core.services.image_engine import ensure_feed_image, ensure_story_image
    from core.services.instagram_api import post_to_instagram
    from core.services.instagram import InstagramService

    if is_story:
        if not ensure_story_image(ad):
            return JsonResponse({'success': False, 'message': 'Failed to generate story image'}, status=500)
        image_path = ad.generated_story_image.path if ad.generated_story_image else None
    else:
        if not ensure_feed_image(ad):
            return JsonResponse({'success': False, 'message': 'Failed to generate feed image'}, status=500)
        image_path = ad.generated_image.path if ad.generated_image else None
    if not image_path:
        return JsonResponse({'success': False, 'message': 'No image available'}, status=500)

    caption = '' if is_story else InstagramService.format_caption(ad, lang='fa')
    result = post_to_instagram(image_path=image_path, caption=caption, is_story=is_story)
    if result.get('success') and result.get('id'):
        if is_story:
            ad.instagram_story_id = result['id']
        else:
            ad.instagram_post_id = result['id']
        ad.is_instagram_published = True
        ad.save(update_fields=['instagram_story_id' if is_story else 'instagram_post_id', 'is_instagram_published'])
    return JsonResponse(result, status=200 if result.get('success') else 500)


SETTINGS_HUB_SECTIONS = ('instagram', 'telegram', 'channels', 'design', 'storage')


@staff_member_required
@require_http_methods(['GET'])
def settings_hub_redirect(request):
    """Redirect /settings/ to first card page (Instagram)."""
    return redirect('settings_hub_instagram')


@staff_member_required
@require_http_methods(['GET'])
def settings_hub_section(request, section):
    """Modular CRM Settings Hub: one card page per section (instagram, telegram, channels, design)."""
    from django.conf import settings as django_settings

    if section not in SETTINGS_HUB_SECTIONS:
        return redirect('settings_hub_section', section='instagram')

    config = SiteConfiguration.get_config()
    env = getattr(django_settings, 'ENVIRONMENT', 'PROD')
    can_edit_restricted = _can_edit_restricted_settings(request.user)

    channels = (
        TelegramChannel.objects.filter(bot_connection__environment=env)
        .select_related('bot_connection')
        .order_by('-is_default', 'title')
    )
    bots = TelegramBot.objects.filter(environment=env).order_by('-is_default', 'name')
    font_options = _available_font_options()

    # Instagram card form (SiteConfiguration fields)
    instagram_form = InstagramBusinessForm(initial={
        'app_id': config.instagram_app_id or '',
        'app_secret': '',  # never prefill
        'instagram_business_id': config.instagram_business_id or '',
        'long_lived_access_token': '',  # never prefill
    })

    # Telegram card: default bot
    default_bot = bots.filter(is_default=True).first() or bots.first()
    if default_bot:
        webhook_display = default_bot.webhook_url or ''
        if not webhook_display and config.production_base_url:
            webhook_display = request.build_absolute_uri(
                reverse('telegram_webhook_by_token', kwargs={'webhook_secret_token': default_bot.webhook_secret_token})
            )
        telegram_form = TelegramBotConfigForm(initial={
            'bot_token': '',
            'bot_username': default_bot.username or '',
            'webhook_url': webhook_display,
        })
    else:
        telegram_form = TelegramBotConfigForm(initial={
            'bot_token': '',
            'bot_username': config.telegram_bot_username or '',
            'webhook_url': config.telegram_webhook_url or config.production_base_url or '',
        })

    # Design card form
    font_choices = [(v, l) for v, l in font_options]
    design_form = DesignDefaultsForm(font_choices=font_choices, instance=config)

    # Instagram token status for badge (check if we have a valid token)
    instagram_token_ok = False
    if config.get_facebook_access_token():
        instagram_token_ok, _ = validate_instagram_token(config.get_facebook_access_token())

    # Token expiry days remaining
    instagram_token_expiry_days = None
    if config.instagram_token_expires_at:
        delta = config.instagram_token_expires_at - timezone.now()
        instagram_token_expiry_days = max(0, delta.days)

    context = {
        'config': config,
        'active_section': section,
        'can_edit_restricted': can_edit_restricted,
        'channels': channels,
        'channel_form': ChannelForm(),
        'bots': bots,
        'default_bot': default_bot,
        'instagram_form': instagram_form,
        'telegram_form': telegram_form,
        'design_form': design_form,
        'font_options': font_options,
        'theme_preference': getattr(config, 'theme_preference', 'light') or 'light',
        'instagram_token_ok': instagram_token_ok,
        'instagram_token_expiry_days': instagram_token_expiry_days,
        'is_instagram_enabled': getattr(config, 'is_instagram_enabled', False),
    }
    return render(request, 'core/settings_hub.html', context)


@staff_member_required
@require_http_methods(['GET'])
def settings_view(request):
    """Legacy CRM control panel (tab-based). Redirects to new hub."""
    return redirect('settings_hub_section', section='instagram')


@staff_member_required
@require_http_methods(['POST'])
def settings_save(request):
    """Save CRM configuration (AJAX). Accepts optional section=instagram|telegram|design for card-specific save."""
    section = (request.POST.get('section') or request.FILES and request.POST.get('section') or '').strip().lower()
    if section and section not in SETTINGS_HUB_SECTIONS:
        section = ''

    if not _can_edit_restricted_settings(request.user):
        restricted_hits = [k for k in RESTRICTED_SETTINGS_KEYS if k in request.POST]
        if restricted_hits:
            return JsonResponse({'status': 'error', 'message': 'Permission denied for restricted settings.'}, status=403)

    config = SiteConfiguration.get_config()
    data = request.POST

    # Section-specific save (card pages)
    if section == 'instagram' and _can_edit_restricted_settings(request.user):
        config.instagram_app_id = (data.get('app_id') or '').strip()[:64] or config.instagram_app_id
        app_secret = (data.get('app_secret') or '').strip()
        if app_secret:
            config.set_instagram_app_secret(app_secret)
        ig_id = (data.get('instagram_business_id') or '').strip()
        if ig_id:
            config.instagram_business_id = ig_id[:64]
        fb_token = (data.get('long_lived_access_token') or '').strip()
        if fb_token:
            config.set_facebook_access_token(fb_token)
        config.save()
        try:
            from django.core.cache import cache
            cache.delete('dashboard_instagram_valid')
        except Exception:
            pass
        log_activity(user=request.user, action='Updated Instagram Business API settings', object_type='SiteConfiguration', object_repr=f'pk={config.pk}')
        return JsonResponse({'status': 'success'})

    if section == 'telegram' and _can_edit_restricted_settings(request.user):
        from django.conf import settings as django_settings
        env = getattr(django_settings, 'ENVIRONMENT', 'PROD')
        default_bot = TelegramBot.objects.filter(environment=env, is_active=True).order_by('-is_default', 'name').first()
        if default_bot:
            token = (data.get('bot_token') or '').strip()
            if token:
                default_bot.set_token(token)
            default_bot.username = (data.get('bot_username') or '').strip()[:64] or default_bot.username
            default_bot.save()
        else:
            config.telegram_bot_username = (data.get('bot_username') or '').strip()[:64] or config.telegram_bot_username
            config.save()
        log_activity(user=request.user, action='Updated Telegram Bot settings', object_type='SiteConfiguration', object_repr=f'pk={config.pk}')
        return JsonResponse({'status': 'success'})

    if section == 'design' and _can_edit_restricted_settings(request.user):
        font_options = _available_font_options()
        design_form = DesignDefaultsForm(font_choices=[(v, l) for v, l in font_options], data=data, files=request.FILES, instance=config)
        if design_form.is_valid():
            design_form.save()
            log_activity(user=request.user, action='Updated Design Defaults', object_type='SiteConfiguration', object_repr=f'pk={config.pk}')
            return JsonResponse({'status': 'success'})
        err = design_form.errors.as_json() if design_form.errors else 'Validation failed'
        return JsonResponse({'status': 'error', 'message': err}, status=400)

    # Legacy / full save (no section or workflow/data)
    if _can_edit_restricted_settings(request.user):
        config.is_ai_enabled = data.get('is_ai_enabled') == 'on'
        config.openai_api_key = (data.get('openai_api_key') or '').strip() or config.openai_api_key
        config.openai_model = (data.get('openai_model') or config.openai_model).strip()
        config.ai_system_prompt = data.get('ai_system_prompt') or config.ai_system_prompt
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
        config.default_font = (data.get('default_font') or config.default_font).strip()[:255]
        try:
            config.default_watermark_opacity = parse_int_in_range(
                data.get('default_watermark_opacity'),
                minimum=0,
                maximum=100,
                field_name='default_watermark_opacity',
            )
        except ValidationError:
            return JsonResponse({'status': 'error', 'message': 'Watermark opacity must be between 0 and 100.'}, status=400)

    config.auto_responder_message = (data.get('auto_responder_message') or config.auto_responder_message).strip()
    config.auto_reply_comments = data.get('auto_reply_comments') == 'on'
    config.auto_reply_dms = data.get('auto_reply_dms') == 'on'
    if 'use_arabic_reshaper' in data:
        config.use_arabic_reshaper = data.get('use_arabic_reshaper') == 'on'
    raw_stages = data.get('workflow_stages_json')
    if raw_stages:
        try:
            import json
            config.workflow_stages = _sanitize_workflow_stages(json.loads(raw_stages))
        except Exception:
            return JsonResponse({'status': 'error', 'message': 'Invalid workflow stages payload.'}, status=400)

    retention_policy = (data.get('retention_policy') or '').strip()
    if retention_policy in dict(SiteConfiguration._meta.get_field('retention_policy').choices):
        config.retention_policy = retention_policy

    config.save()
    log_activity(
        user=request.user,
        action='Updated CRM settings',
        object_type='SiteConfiguration',
        object_repr=f'pk={config.pk}',
    )
    return JsonResponse({'status': 'success'})


@staff_member_required
def settings_invite_member(request):
    """Invite a team member: GET shows form page, POST creates user and redirects to settings."""
    if not _can_edit_restricted_settings(request.user):
        return HttpResponseForbidden('Permission denied.')

    if request.method == 'GET':
        return render(request, 'core/settings_invite_member.html', {'error': None})

    User = get_user_model()
    email = (request.POST.get('email') or '').strip().lower()
    role = (request.POST.get('role') or 'viewer').strip().lower()
    if not email or '@' not in email:
        return render(request, 'core/settings_invite_member.html', {
            'error': 'Valid email is required.',
            'email': email,
            'role': role,
        })
    if role not in ('admin', 'editor', 'viewer'):
        role = 'viewer'

    existing = User.objects.filter(email__iexact=email).first()
    if existing:
        return render(request, 'core/settings_invite_member.html', {
            'error': 'A user with this email already exists.',
            'email': email,
            'role': role,
        })

    username_base = email.split('@')[0][:24] or 'member'
    username = username_base
    idx = 1
    while User.objects.filter(username__iexact=username).exists():
        idx += 1
        username = f'{username_base}{idx}'

    temp_password = uuid.uuid4().hex[:12]
    user = User.objects.create_user(username=username, email=email, password=temp_password)
    user.is_staff = True
    user.is_superuser = role == 'admin'
    user.save(update_fields=['is_staff', 'is_superuser'])

    perm = Permission.objects.filter(codename='can_edit_settings').first()
    if perm:
        if role == 'editor':
            user.user_permissions.add(perm)
        else:
            user.user_permissions.remove(perm)

    try:
        from django.core.mail import send_mail
        send_mail(
            subject='Iraniu CRM Invitation',
            message=(
                f'You were invited to Iraniu CRM as {role.title()}.\n'
                f'Username: {username}\n'
                f'Temporary password: {temp_password}\n'
                'Please change your password after login.'
            ),
            from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', None),
            recipient_list=[email],
            fail_silently=True,
        )
    except Exception:
        pass

    log_activity(
        user=request.user,
        action='Invited team member',
        object_type='User',
        object_repr=f'{username} ({role.title()})',
        metadata={'email': email, 'role': role},
    )
    messages.success(request, f'Invitation created for {email}.')
    return redirect(reverse('settings') + '?tab=team')


@staff_member_required
@require_http_methods(['GET'])
def export_ads_csv(request):
    """Export all ads as CSV (streaming response)."""
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="ads_export_{timezone.now().date()}.csv"'
    writer = csv.writer(response)
    writer.writerow(['UUID', 'Category', 'Status', 'Content', 'Created At', 'Updated At'])
    for ad in AdRequest.objects.select_related('category').order_by('-created_at').iterator():
        writer.writerow([
            str(ad.uuid),
            ad.get_category_display(),
            ad.status,
            (ad.content or '').replace('\n', ' ')[:1000],
            ad.created_at.isoformat(),
            ad.updated_at.isoformat(),
        ])
    log_activity(user=request.user, action='Exported ads CSV', object_type='AdRequest', object_repr='All ads')
    return response


@staff_member_required
@require_http_methods(['GET'])
def export_users_excel(request):
    """Export user list in Excel-compatible TSV format."""
    User = get_user_model()
    response = HttpResponse(content_type='application/vnd.ms-excel; charset=utf-8')
    response['Content-Disposition'] = f'attachment; filename="users_export_{timezone.now().date()}.xls"'
    response.write('Username\tEmail\tRole\tIs Staff\tLast Login\n')
    for user in User.objects.all().order_by('username').iterator():
        response.write(
            f'{user.username}\t{user.email or ""}\t{_role_label(user)}\t'
            f'{"Yes" if user.is_staff else "No"}\t{user.last_login or ""}\n'
        )
    log_activity(user=request.user, action='Exported users Excel', object_type='User', object_repr='All users')
    return response


@staff_member_required
def cleanup_generated_media(request):
    """
    Storage cleanup: GET shows confirmation page, POST executes deletion.

    Only superusers and staff with ``can_edit_settings`` may trigger this.
    """
    from core.services.image_engine import delete_old_assets

    # ── Permission gate: Admin / Superuser only ──
    if not (request.user.is_superuser or request.user.has_perm('core.can_edit_settings')):
        return HttpResponseForbidden('Permission denied. Admin privileges required.')

    config = SiteConfiguration.get_config()

    # ── GET: show confirmation page ──
    if request.method == 'GET':
        raw_days = request.GET.get('days', '').strip()
        try:
            days = int(raw_days) if raw_days else config.cleanup_retention_days
        except (TypeError, ValueError):
            days = config.cleanup_retention_days
        days = max(1, days)
        return render(request, 'core/settings_cleanup_confirm.html', {
            'days': days,
            'config': config,
        })

    # ── POST: execute cleanup ──
    raw_days = request.POST.get('days', '').strip()
    try:
        days = int(raw_days) if raw_days else config.cleanup_retention_days
    except (TypeError, ValueError):
        days = config.cleanup_retention_days
    days = max(1, days)

    # Persist the retention days setting if changed
    if days != config.cleanup_retention_days:
        config.cleanup_retention_days = days
        config.save(update_fields=['cleanup_retention_days', 'updated_at'])

    # Delegate to the service function
    result = delete_old_assets(days=days)

    # Audit log
    log_activity(
        user=request.user,
        action='Manual storage cleanup',
        object_type='Media',
        object_repr='generated_ads',
        metadata={
            'days': days,
            'deleted_count': result['deleted_count'],
            'freed_space_mb': result['freed_space_mb'],
            'errors': result['errors'],
        },
    )

    return render(request, 'core/settings_cleanup_confirm.html', {
        'days': days,
        'config': config,
        'result': result,
    })


@staff_member_required
def reset_all_settings(request):
    """Danger zone: GET shows confirm page, POST resets config and redirects to settings."""
    if not _can_edit_restricted_settings(request.user):
        return HttpResponseForbidden('Permission denied.')

    if request.method == 'GET':
        return render(request, 'core/settings_reset_confirm.html', {'error': None})

    if (request.POST.get('confirm_text') or '').strip() != 'DELETE':
        return render(request, 'core/settings_reset_confirm.html', {
            'error': 'Type DELETE to confirm.',
        })

    config = SiteConfiguration.get_config()
    default_cfg = SiteConfiguration()
    fields_to_reset = [
        'is_ai_enabled',
        'openai_model',
        'ai_system_prompt',
        'approval_message_template',
        'rejection_message_template',
        'submission_ack_message',
        'production_base_url',
        'instagram_business_id',
        'default_font',
        'default_watermark_opacity',
        'workflow_stages',
        'auto_responder_message',
        'auto_reply_comments',
        'auto_reply_dms',
        'retention_policy',
    ]
    for field in fields_to_reset:
        setattr(config, field, getattr(default_cfg, field))
    config.save()

    log_activity(user=request.user, action='Reset all settings', object_type='SiteConfiguration', object_repr=f'pk={config.pk}')
    messages.success(request, 'Settings reset to default values.')
    return redirect(reverse('settings') + '?tab=data')


@staff_member_required
@require_http_methods(['GET', 'POST'])
def settings_change_password(request):
    """Change password for current user. GET: form page; POST: validate & update."""
    from django.contrib.auth.password_validation import validate_password
    from django.core.exceptions import ValidationError as DjangoValidationError

    if request.method == 'GET':
        return render(request, 'core/settings_change_password.html', {'error': None})

    old = (request.POST.get('old_password') or '').strip()
    new1 = (request.POST.get('new_password1') or '').strip()
    new2 = (request.POST.get('new_password2') or '').strip()
    ctx = {}
    if not old:
        ctx['error'] = 'Current password is required.'
    elif not request.user.check_password(old):
        ctx['error'] = 'Current password is incorrect.'
    elif not new1:
        ctx['error'] = 'New password is required.'
    elif new1 != new2:
        ctx['error'] = 'New passwords do not match.'
    else:
        try:
            validate_password(new1, request.user)
        except DjangoValidationError as e:
            ctx['error'] = '; '.join(e.messages)
    if ctx.get('error'):
        return render(request, 'core/settings_change_password.html', ctx)
    request.user.set_password(new1)
    request.user.save(update_fields=['password'])
    from django.contrib.auth import update_session_auth_hash
    update_session_auth_hash(request, request.user)
    messages.success(request, 'Password changed successfully.')
    return redirect('settings')


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
def notification_mark_read(request, pk):
    """Mark one notification as read (AJAX)."""
    notification = get_object_or_404(Notification, pk=pk)
    notification.is_read = True
    notification.save(update_fields=['is_read'])
    try:
        from django.core.cache import cache
        cache.delete('navbar_notifications')
    except Exception:
        pass
    return JsonResponse({'status': 'success'})


@staff_member_required
@require_http_methods(['POST'])
def notification_mark_all_read(request):
    """Mark all notifications as read (AJAX)."""
    Notification.objects.filter(is_read=False).update(is_read=True)
    try:
        from django.core.cache import cache
        cache.delete('navbar_notifications')
    except Exception:
        pass
    return JsonResponse({'status': 'success'})


@staff_member_required
@require_http_methods(['POST'])
def check_instagram_connection(request):
    """Check Instagram/Meta Graph API token (AJAX). Uses token from POST or from SiteConfiguration."""
    token = (request.POST.get('long_lived_access_token') or request.POST.get('access_token') or '').strip()
    if not token:
        config = SiteConfiguration.get_config()
        token = config.get_facebook_access_token() or ''
    ok, msg = validate_instagram_token(token)
    return JsonResponse({'success': ok, 'message': msg})


@staff_member_required
@require_http_methods(['GET'])
def instagram_connect(request):
    """
    Initiate Instagram OAuth flow. Redirects user to Meta's authorization page.
    Generates a CSRF state token and stores it in SiteConfiguration.
    """
    from core.services.instagram_oauth import generate_oauth_state, build_authorization_url

    config = SiteConfiguration.get_config()
    app_id = (config.instagram_app_id or '').strip()
    if not app_id:
        messages.error(request, 'Instagram App ID is not configured. Save it first in the Instagram Settings card.')
        return redirect('settings_hub_instagram')

    app_secret = config.get_instagram_app_secret()
    if not app_secret:
        messages.error(request, 'Instagram App Secret is not configured. Save it first in the Instagram Settings card.')
        return redirect('settings_hub_instagram')

    # Build redirect URI: use settings.INSTAGRAM_REDIRECT_URI so it matches Meta Developer portal exactly
    redirect_uri = getattr(settings, 'INSTAGRAM_REDIRECT_URI', None) or request.build_absolute_uri(reverse('instagram_oauth_callback'))
    if not redirect_uri.startswith('https://'):
        redirect_uri = request.build_absolute_uri(reverse('instagram_oauth_callback'))

    # Generate and store CSRF state
    state = generate_oauth_state()
    config.instagram_oauth_state = state
    config.save(update_fields=['instagram_oauth_state'])

    auth_url = build_authorization_url(app_id, redirect_uri, state)
    log_activity(
        user=request.user,
        action='Initiated Instagram OAuth flow',
        object_type='SiteConfiguration',
        object_repr=f'pk={config.pk}',
    )
    return redirect(auth_url)


@staff_member_required
@require_http_methods(['GET'])
def instagram_callback(request):
    """
    Instagram OAuth callback. Meta redirects here with ?code=AUTH_CODE&state=STATE.
    Validates state, exchanges code for long-lived token, and saves to DB.
    """
    from core.services.instagram_oauth import perform_full_oauth_exchange
    from core.notifications import send_notification

    error = request.GET.get('error')
    error_reason = request.GET.get('error_reason', '')
    error_description = request.GET.get('error_description', '')
    if error:
        msg = f'Instagram authorization denied: {error_description or error_reason or error}'
        logger.warning('Instagram OAuth callback error: %s', msg)
        messages.error(request, msg)
        return redirect('settings_hub_instagram')

    code = (request.GET.get('code') or '').strip()
    state = (request.GET.get('state') or '').strip()

    if not code:
        messages.error(request, 'Instagram OAuth callback: no authorization code received.')
        return redirect('settings_hub_instagram')

    # Validate CSRF state
    config = SiteConfiguration.get_config()
    stored_state = (config.instagram_oauth_state or '').strip()
    if not stored_state or state != stored_state:
        logger.warning('Instagram OAuth state mismatch: expected=%s got=%s', stored_state, state)
        messages.error(request, 'Instagram OAuth security check failed (state mismatch). Please try again.')
        config.instagram_oauth_state = ''
        config.save(update_fields=['instagram_oauth_state'])
        return redirect('settings_hub_instagram')

    # Build redirect_uri (must be identical to the one used in the authorization request)
    redirect_uri = getattr(settings, 'INSTAGRAM_REDIRECT_URI', None) or request.build_absolute_uri(reverse('instagram_oauth_callback'))
    if not redirect_uri.startswith('https://'):
        redirect_uri = request.build_absolute_uri(reverse('instagram_oauth_callback'))

    result = perform_full_oauth_exchange(code, redirect_uri)

    if result.get('success'):
        messages.success(request, result.get('message', 'Instagram connected successfully.'))
        send_notification(
            level='success',
            message=f"Instagram connected. Token expires {result.get('expires_at', '?')}.",
            link=reverse('settings_hub_instagram'),
        )
        log_activity(
            user=request.user,
            action='Instagram OAuth completed — long-lived token saved',
            object_type='SiteConfiguration',
            object_repr=f'ig_user_id={result.get("ig_user_id", "?")}',
        )
    else:
        err = result.get('error', 'Unknown error')
        messages.error(request, f'Instagram OAuth failed: {err}')
        send_notification(
            level='error',
            message=f'Instagram OAuth failed: {err}',
            link=reverse('settings_hub_instagram'),
        )

    return redirect('settings_hub_instagram')


@staff_member_required
@require_http_methods(['POST'])
def instagram_check_permissions(request):
    """Dry-run: check which permissions the current Instagram token has (AJAX)."""
    from core.services.instagram_oauth import check_token_permissions

    config = SiteConfiguration.get_config()
    token = config.get_facebook_access_token() or ''
    if not token:
        return JsonResponse({'success': False, 'message': 'No access token configured.'})

    result = check_token_permissions(token)
    if result.get('success'):
        perms = result.get('permissions', [])
        has_publish = result.get('has_publish', False)
        return JsonResponse({
            'success': True,
            'permissions': perms,
            'has_publish': has_publish,
            'message': f"Granted: {', '.join(perms) if perms else 'none'}. "
                       f"{'✅ Can publish.' if has_publish else '⚠️ instagram_content_publish NOT granted.'}",
        })
    return JsonResponse({'success': False, 'message': result.get('error', 'Check failed.')})


@csrf_exempt
@require_http_methods(['GET', 'POST'])
def instagram_webhook(request):
    """
    Public Instagram webhook (no @login_required). Meta sends GET for verify, POST for payloads.
    GET with hub.mode=subscribe & hub.verify_token & hub.challenge: return challenge if token matches (200).
    GET without params (e.g. browser): return 200 with short text so endpoint is reachable.
    GET with wrong verify_token: return 403.
    POST: log body and return 200.
    """
    logger_ig = logging.getLogger('core.services.instagram')
    if request.method == 'GET':
        hub_mode = request.GET.get('hub.mode', '').strip()
        hub_verify_token = request.GET.get('hub.verify_token', '').strip()
        hub_challenge = request.GET.get('hub.challenge', '').strip()
        expected_token = getattr(settings, 'INSTAGRAM_WEBHOOK_VERIFY_TOKEN', '') or ''
        if hub_mode == 'subscribe' and hub_verify_token == expected_token and hub_challenge:
            logger_ig.info('Instagram webhook verified: challenge returned.')
            return HttpResponse(hub_challenge, content_type='text/plain', status=200)
        if hub_verify_token and hub_verify_token != expected_token:
            logger_ig.warning('Instagram webhook GET verification failed: token mismatch.')
            return HttpResponseForbidden()
        # No params or missing challenge: e.g. browser visit. Return 200 so URL is not 403.
        return HttpResponse('Instagram webhook endpoint.', content_type='text/plain', status=200)
    if request.method == 'POST':
        try:
            body = request.body.decode('utf-8') if request.body else ''
            logger_ig.info('Instagram webhook POST: %s', body[:2000] if len(body) > 2000 else body)
        except Exception as e:
            logger_ig.exception('Instagram webhook POST read error: %s', e)
        return HttpResponse(status=200)


@staff_member_required
@require_http_methods(['POST'])
def test_telegram(request):
    """Test Telegram connection (AJAX). Does not save token. Returns bot_info (getMe) for display."""
    token = (request.POST.get('telegram_bot_token') or request.POST.get('bot_token') or '').strip()
    success, bot_info, error = telegram_get_me(token)
    if success and bot_info:
        return JsonResponse({
            'success': True,
            'message': f"Connected as @{bot_info.get('username', '?')}",
            'bot_info': {
                'id': bot_info.get('id'),
                'username': bot_info.get('username'),
                'first_name': bot_info.get('first_name'),
                'is_bot': bot_info.get('is_bot'),
            },
        })
    return JsonResponse({'success': False, 'message': error or 'Invalid token'})


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
    except ValidationError as exc:
        msg = exc.messages[0] if exc.messages else 'Invalid content.'
        return JsonResponse({'status': 'error', 'message': msg}, status=400)
    except Exception as exc:
        logger.exception("approve_ad failed: %s", exc)
        return _json_server_error('Could not approve ad.')


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
    except Exception as exc:
        logger.exception("reject_ad failed: %s", exc)
        return _json_server_error('Could not reject ad.')


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
            except ValidationError as ve:
                return JsonResponse({
                    'status': 'error',
                    'message': ve.messages[0] if ve.messages else 'Invalid content.',
                }, status=400)
            except AdRequest.DoesNotExist:
                pass
        return JsonResponse({'status': 'success', 'approved_count': approved_count})
    except Exception as exc:
        logger.exception("bulk_approve failed: %s", exc)
        return _json_server_error('Bulk approve failed.')


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
    except Exception as exc:
        logger.exception("bulk_reject failed: %s", exc)
        return _json_server_error('Bulk reject failed.')


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
        name_fa = (request.POST.get('name_fa') or '').strip()
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
            name=name, name_fa=name_fa, slug=slug, color=color or '#7C4DFF',
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
        name_fa = (request.POST.get('name_fa') or '').strip()
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
        category.name_fa = name_fa
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
        'default_font': config.default_font,
        'default_watermark_opacity': config.default_watermark_opacity,
        'workflow_stages': config.workflow_stages,
        'auto_responder_message': config.auto_responder_message,
        'auto_reply_comments': config.auto_reply_comments,
        'auto_reply_dms': config.auto_reply_dms,
        'retention_policy': config.retention_policy,
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
        try:
            from core.validators import validate_ad_content as validate_ad_content_rules
            validate_ad_content_rules(content)
        except ValidationError as e:
            msg = e.messages[0] if e.messages else 'Invalid content.'
            return JsonResponse({'error': msg}, status=400)
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
    except Exception as exc:
        logger.exception("submit_ad failed: %s", exc)
        return JsonResponse({'error': 'Submission failed.'}, status=500)


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
        if 'default_font' in data:
            config.default_font = str(data['default_font'])[:255]
        if 'default_watermark_opacity' in data:
            try:
                config.default_watermark_opacity = max(0, min(100, int(data['default_watermark_opacity'])))
            except (TypeError, ValueError):
                pass
        if 'workflow_stages' in data:
            config.workflow_stages = _sanitize_workflow_stages(data['workflow_stages'])
        if 'auto_responder_message' in data:
            config.auto_responder_message = str(data['auto_responder_message'])
        if 'auto_reply_comments' in data:
            config.auto_reply_comments = bool(data['auto_reply_comments'])
        if 'auto_reply_dms' in data:
            config.auto_reply_dms = bool(data['auto_reply_dms'])
        if 'retention_policy' in data and str(data['retention_policy']) in dict(SiteConfiguration._meta.get_field('retention_policy').choices):
            config.retention_policy = str(data['retention_policy'])
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
def api_instagram_post(request):
    """
    POST /api/instagram/post/
    Body: { image_url, caption } or { image_url, message_text, email, phone }
    Optional: scheduled_at (ISO) — schedules instead of posting immediately.
    Returns JSON { success, message, id } or { scheduled: true, pk }.
    """
    # Guard: check if Instagram is enabled
    config = SiteConfiguration.get_config()
    if not getattr(config, 'is_instagram_enabled', False):
        return JsonResponse({'success': False, 'message': 'Instagram is not enabled. Complete Instagram settings first.'}, status=400)

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


# ---------- API Management (webhook + API clients + delivery log) ----------

@staff_member_required
@require_http_methods(['GET'])
def settings_api(request):
    """API Management: webhook settings, API clients list, last webhook deliveries."""
    config = SiteConfiguration.get_config()
    clients = ApiClient.objects.all().order_by('name')
    webhook_deliveries = (
        DeliveryLog.objects.filter(channel=DeliveryLog.Channel.WEBHOOK)
        .select_related('ad')
        .order_by('-created_at')[:25]
    )
    return render(request, 'core/settings_api.html', {
        'clients': clients,
        'config': config,
        'webhook_deliveries': webhook_deliveries,
    })


@staff_member_required
@require_http_methods(['POST'])
def settings_api_save_webhook(request):
    """Save external webhook URL, enable flag, and secret key from API Management form."""
    config = SiteConfiguration.get_config()
    config.external_webhook_url = (request.POST.get('external_webhook_url') or '').strip()
    config.enable_webhook_sync = request.POST.get('enable_webhook_sync') == 'on'
    new_secret = (request.POST.get('webhook_secret_key') or '').strip()
    if new_secret:
        config.webhook_secret_key = new_secret
    config.save()
    messages.success(request, 'Webhook settings saved.')
    return redirect('settings_api')


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
    validate_uploaded_image(uploaded_file, max_size_bytes=8 * 1024 * 1024, field_name='Temporary background')
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
    except Exception as exc:
        logger.warning("Failed to save temp test background: %s", exc)
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
        messages.success(request, f'Template "{template.name}" created. Position labels in the Manual Editor.')
        return redirect(reverse('template_manual_edit', args=[template.pk]))
    return render(request, 'core/template_create.html', {'form': form})


@staff_member_required
@require_http_methods(['GET', 'POST'])
def template_manual_edit(request, template_id):
    """High-precision manual editor with numeric controls and live canvas preview. Supports POST and STORY layouts."""
    from PIL import Image
    from core.models import (
        default_story_coordinates,
        FORMAT_POST, FORMAT_STORY, FORMAT_DIMENSIONS,
        STORY_SAFE_TOP, STORY_SAFE_BOTTOM,
    )
    from core.services.image_engine import get_story_coordinates

    template = get_object_or_404(AdTemplate, pk=template_id)

    # Get background image URL and dimensions
    background_url = ''
    img_width, img_height = 1080, 1080
    if template.background_image:
        try:
            background_url = template.background_image.url
            with template.background_image.open('rb') as fh:
                img = Image.open(fh)
                img_width, img_height = img.size
        except Exception:
            pass

    if not background_url:
        from django.templatetags.static import static
        default_rel = "static/images/default_template/Template.png"
        default_path = Path(settings.BASE_DIR) / default_rel
        if default_path.exists():
            try:
                static_url = static('images/default_template/Template.png')
                if static_url:
                    background_url = request.build_absolute_uri(static_url)
                with open(default_path, 'rb') as fh:
                    img = Image.open(fh)
                    img_width, img_height = img.size
            except Exception:
                pass

    # Determine which layout mode is active (POST or STORY)
    layout_mode = (request.GET.get('layout') or request.POST.get('layout_mode') or 'POST').upper()
    if layout_mode not in (FORMAT_POST, FORMAT_STORY):
        layout_mode = FORMAT_POST

    is_story = layout_mode == FORMAT_STORY

    # Canvas dimensions for the active layout
    if is_story:
        canvas_w, canvas_h = FORMAT_DIMENSIONS[FORMAT_STORY]
    else:
        canvas_w, canvas_h = img_width, img_height

    # Load current coordinates (merge with defaults)
    coords = default_adtemplate_coordinates()
    user_coords = template.coordinates or {}
    if isinstance(user_coords, dict):
        for key in coords.keys():
            if isinstance(user_coords.get(key), dict):
                coords[key].update({k: v for k, v in user_coords[key].items() if v is not None})

    # Load story coordinates
    story_coords = default_story_coordinates()
    user_story_coords = template.story_coordinates or {}
    if isinstance(user_story_coords, dict):
        for key in story_coords.keys():
            if isinstance(user_story_coords.get(key), dict):
                story_coords[key].update({k: v for k, v in user_story_coords[key].items() if v is not None})

    # Active coords for the current layout mode
    active_coords = story_coords if is_story else coords

    def _parse_align(value: str, default: str = 'right') -> str:
        v = (value or '').strip().lower()
        return v if v in ('left', 'center', 'right') else default

    def _layer_config(prefix: str, base: dict, *, include_max_width: bool = False, ref_w: int = 1080, ref_h: int = 1080) -> dict:
        min_x, max_x = -ref_w, ref_w * 2
        min_y, max_y = -ref_h, ref_h * 2
        parsed = {
            'x': parse_int_in_range(
                request.POST.get(f'{prefix}_x', base.get('x', 0)),
                field_name=f'{prefix} x',
                minimum=min_x,
                maximum=max_x,
            ),
            'y': parse_int_in_range(
                request.POST.get(f'{prefix}_y', base.get('y', 0)),
                field_name=f'{prefix} y',
                minimum=min_y,
                maximum=max_y,
            ),
            'size': parse_int_in_range(
                request.POST.get(f'{prefix}_size', base.get('size', 24)),
                field_name=f'{prefix} size',
                minimum=1,
                maximum=600,
            ),
            'color': parse_hex_color(
                request.POST.get(f'{prefix}_color', base.get('color', '#FFFFFF')),
                field_name=f'{prefix} color',
                default=str(base.get('color') or '#FFFFFF'),
            ),
            'font_path': (request.POST.get(f'{prefix}_font_path') or base.get('font_path') or '').strip(),
            'align': _parse_align(request.POST.get(f'{prefix}_align', base.get('align', 'right')), default='right'),
            'bold': request.POST.get(f'{prefix}_bold', '1' if base.get('bold') else '0') == '1',
        }
        if include_max_width:
            parsed['max_width'] = parse_int_in_range(
                request.POST.get(f'{prefix}_max_width', base.get('max_width', max(200, int(ref_w * 0.6)))),
                field_name=f'{prefix} max width',
                minimum=1,
                maximum=ref_w * 2,
            )
        return parsed

    if request.method == 'POST':
        action = request.POST.get('action', 'save').strip().lower()
        if action in ('save', 'save_test'):
            try:
                new_coords = {
                    'category': _layer_config('cat', active_coords.get('category', {}), include_max_width=True, ref_w=canvas_w, ref_h=canvas_h),
                    'description': _layer_config('desc', active_coords.get('description', {}), include_max_width=True, ref_w=canvas_w, ref_h=canvas_h),
                    'phone': _layer_config('phone', active_coords.get('phone', {}), include_max_width=True, ref_w=canvas_w, ref_h=canvas_h),
                }
            except ValidationError as exc:
                return JsonResponse({'status': 'error', 'errors': [str(exc)]}, status=400)

            # Save to the appropriate coordinates field
            if is_story:
                template.story_coordinates = new_coords
                template.save(update_fields=['story_coordinates', 'updated_at'])
            else:
                template.coordinates = new_coords
                template.save(update_fields=['coordinates', 'updated_at'])

            if action == 'save_test':
                from core.services.image_engine import create_ad_image
                test_category = (request.POST.get('preview_category') or 'دسته‌بندی').strip()
                test_desc = (request.POST.get('preview_description') or 'متن نمونه برای تست نمایش.').strip()
                test_phone = (request.POST.get('preview_phone') or '+98 912 345 6789').strip()
                test_path = create_ad_image(
                    template.pk,
                    category=test_category,
                    text=test_desc,
                    phone=test_phone,
                    format_type=layout_mode,
                )
                if test_path:
                    media_url = (getattr(settings, 'MEDIA_URL', '/media/') or '/media/').rstrip('/')
                    rel = Path(test_path).name
                    test_url = f"{request.scheme}://{request.get_host()}{media_url}/generated_ads/{rel}"
                    return JsonResponse({
                        'status': 'success',
                        'message': f'{"Story" if is_story else "Post"} coordinates saved and test image generated.',
                        'test_image_url': test_url,
                    })
                return JsonResponse({
                    'status': 'success',
                    'message': f'{"Story" if is_story else "Post"} coordinates saved, but test image generation failed.',
                })

            return JsonResponse({'status': 'success', 'message': f'{"Story" if is_story else "Post"} coordinates saved successfully.'})

        # Auto-generate story coords from post coords
        if action == 'auto_story':
            auto_story = get_story_coordinates(coords, img_width, img_height)
            template.story_coordinates = auto_story
            template.save(update_fields=['story_coordinates', 'updated_at'])
            return JsonResponse({'status': 'success', 'message': 'Story coordinates auto-generated from post layout.', 'coords': auto_story})

    context = {
        'template': template,
        'background_url': background_url,
        'img_width': img_width,
        'img_height': img_height,
        'canvas_w': canvas_w,
        'canvas_h': canvas_h,
        'layout_mode': layout_mode,
        'is_story': is_story,
        'story_safe_top': STORY_SAFE_TOP,
        'story_safe_bottom': STORY_SAFE_BOTTOM,
        'coords': active_coords,
        'cat': active_coords.get('category', {}),
        'desc': active_coords.get('description', {}),
        'phone_coord': active_coords.get('phone', {}),
        'default_coords_json': json.dumps(default_adtemplate_coordinates()),
        'default_story_coords_json': json.dumps(default_story_coordinates()),
    }
    return render(request, 'core/template_manual_edit.html', context)


@staff_member_required
@require_http_methods(['GET', 'POST'])
def template_tester(request):
    """Preview ad image: select template, dummy text, and optional custom background. POST generates with optional upload."""
    templates = AdTemplate.objects.filter(is_active=True).only('pk', 'name').order_by('name')
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
                    try:
                        temp_bg_path = _save_temp_test_background(uploaded)
                    except ValidationError as exc:
                        validation_error = str(exc)
                        temp_bg_path = None
                    if temp_bg_path:
                        background_file = temp_bg_path
                        used_custom_background = True
                        request.session[COORD_LAB_TEMP_BACKGROUND_KEY] = temp_bg_path

                from core.services.image_engine import create_ad_image
                from core.models import FORMAT_POST, FORMAT_STORY
                fmt = (request.POST.get('format_type') or FORMAT_POST).upper()
                if fmt not in (FORMAT_POST, FORMAT_STORY):
                    fmt = FORMAT_POST
                path_str = create_ad_image(
                    int(template_id),
                    category_text,
                    ad_text,
                    phone_number,
                    background_file=background_file,
                    format_type=fmt,
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

    from core.models import FORMAT_POST, FORMAT_STORY
    fmt = (request.GET.get('format_type') or FORMAT_POST).upper()
    if fmt not in (FORMAT_POST, FORMAT_STORY):
        fmt = FORMAT_POST
    path_str = create_ad_image(int(template_id), category_text, ad_text, phone_number, format_type=fmt)
    if not path_str:
        return HttpResponse(status=500)
    with open(path_str, 'rb') as f:
        return HttpResponse(f.read(), content_type='image/png')


# ---------- Channel Manager (dashboard/channels/) ----------

@staff_member_required
def channel_list(request):
    """
    List all Telegram channels. Staff/superuser only.
    Create new channel via channel_create page (dashboard/channels/create/).
    """
    from django.conf import settings
    env = getattr(settings, "ENVIRONMENT", "PROD")
    channels = (
        TelegramChannel.objects.filter(bot_connection__environment=env)
        .select_related("bot_connection")
        .order_by("-is_default", "title")
    )
    context = {
        "channels": channels,
    }
    return render(request, "dashboard/channels.html", context)


@staff_member_required
@require_http_methods(['GET', 'POST'])
def channel_create(request):
    """
    Create a new Telegram channel. GET: form page; POST: create and redirect.
    """
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
    next_url = request.GET.get('next', '').strip()
    context = {
        "form": form,
        "next_url": next_url,
    }
    return render(request, "dashboard/channel_form.html", context)


@staff_member_required
@require_http_methods(['POST'])
def channel_delete(request, pk):
    """Delete a channel. Staff only. Redirects to channel_list with message."""
    channel = get_object_or_404(TelegramChannel, pk=pk)
    title = channel.title
    channel.delete()
    messages.success(request, f'Channel "{title}" has been deleted.')
    next_url = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if next_url:
        return redirect(next_url)
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
    next_url = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if next_url:
        return redirect(next_url)
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
        next_url = (request.POST.get('next') or request.GET.get('next') or '').strip()
        if next_url:
            return redirect(next_url)
        return redirect("channel_list")
    if not token:
        messages.error(request, "Bot has no token configured.")
        next_url = (request.POST.get('next') or request.GET.get('next') or '').strip()
        if next_url:
            return redirect(next_url)
        return redirect("channel_list")
    try:
        chat_id = int(channel.channel_id.strip())
    except (ValueError, TypeError):
        messages.error(request, "Invalid channel ID.")
        next_url = (request.POST.get('next') or request.GET.get('next') or '').strip()
        if next_url:
            return redirect(next_url)
        return redirect("channel_list")
    text = "🔔 Test message from Iraniu Channel Manager — if you see this, the bot has admin rights."
    success, _, api_error = send_message(token, chat_id, text)
    if success:
        messages.success(request, f'Test message sent to "{channel.title}".')
    else:
        messages.error(request, api_error or "Failed to send test message. Check bot admin rights and channel ID.")
    next_url = (request.POST.get('next') or request.GET.get('next') or '').strip()
    if next_url:
        return redirect(next_url)
    return redirect("channel_list")
