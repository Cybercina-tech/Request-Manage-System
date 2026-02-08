"""
Iraniu — Staff-only views. Request/response only; business logic in services.
"""

import logging
import os
import signal
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth import get_user_model
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.csrf import csrf_exempt
from django.shortcuts import render, get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.core.paginator import Paginator

from .models import (
    AdRequest,
    AdminProfile,
    Category,
    SiteConfiguration,
    TelegramBot,
    TelegramUser,
    InstagramConfiguration,
    ApiClient,
    DeliveryLog,
    ScheduledInstagramPost,
    REJECTION_REASONS,
    REJECTION_REASONS_DETAIL,
)
from .services import (
    clean_ad_text,
    run_ai_moderation,
    test_telegram_connection,
    test_openai_connection,
    get_webhook_info,
    set_webhook,
    delete_webhook,
)
from .services.dashboard import get_dashboard_context, get_pulse_data
from .services.ad_actions import approve_one_ad, reject_one_ad, request_revision_one_ad
from .view_utils import get_request_payload, parse_request_json

logger = logging.getLogger(__name__)


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
@require_http_methods(['GET'])
def settings_view(request):
    """Settings page: tabs for AI, Telegram, Maintenance."""
    config = SiteConfiguration.get_config()
    context = {'config': config}
    return render(request, 'core/settings.html', context)


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
    config.save()
    return JsonResponse({'status': 'success'})


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
        body = parse_request_json(request) or request.POST.dict()
        ad_id = body.get('ad_id') or request.POST.get('ad_id')
        edited_content = (body.get('content') or request.POST.get('content', '')).strip() or None
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
        body = parse_request_json(request) or request.POST.dict()
        ad_id = body.get('ad_id') or request.POST.get('ad_id')
        reason = (body.get('reason') or request.POST.get('reason', '')).strip()
        comment = (body.get('comment') or request.POST.get('comment', '')).strip()
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
    """Bulk approve: ad_ids list in JSON. Returns { status, approved_count }. """
    try:
        body = parse_request_json(request) or request.POST.dict()
        ad_ids = body.get('ad_ids') or request.POST.getlist('ad_ids') or []
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
        body = parse_request_json(request) or request.POST.dict()
        ad_ids = body.get('ad_ids') or request.POST.getlist('ad_ids') or []
        reason = (body.get('reason') or request.POST.get('reason') or '').strip()
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
    from core.models import TelegramBot
    from core.bot_handler import send_message_to_chat
    default_bot = TelegramBot.objects.filter(is_default=True, is_active=True).first()
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
        data = parse_request_json(request) or request.POST.dict()
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
    """Import configuration from JSON (merge non-secret fields)."""
    try:
        data = parse_request_json(request)
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
    """List all bots. Default bot in System Core card; others in table."""
    default_bot = TelegramBot.objects.filter(is_default=True).first()
    other_bots = TelegramBot.objects.exclude(is_default=True).order_by('name')
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

    data = parse_request_json(request) or request.POST.dict()
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
@require_http_methods(['GET', 'POST'])
def settings_api_edit(request, pk=None):
    """Create or edit API client. New key shown once on create."""
    from core.encryption import hash_api_key
    import secrets

    client = get_object_or_404(ApiClient, pk=pk) if pk else None
    new_key_plain = None
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            return JsonResponse({'status': 'error', 'message': 'Name is required'}, status=400)
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
            return JsonResponse({
                'status': 'success',
                'redirect': reverse('settings_api'),
                'new_key': new_key_plain,
                'message': 'Save successful. Copy the API key now; it will not be shown again.',
            })
        return JsonResponse({'status': 'success', 'redirect': reverse('settings_api')})
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
