"""
Iraniu — Dashboard and pulse data. Single source of truth for KPIs and health.
"""

import json
from datetime import timedelta
from django.utils import timezone

from core.models import AdRequest, AdTemplate, TelegramChannel, SiteConfiguration


def get_pulse_data():
    """
    Compute live stats for dashboard polling.
    Returns dict: total, pending_ai, pending_manual, approved_today, rejected_today,
    rejection_rate, pulse_score, system_health.
    """
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    total = AdRequest.objects.count()
    pending_ai = AdRequest.objects.filter(status=AdRequest.Status.PENDING_AI).count()
    pending_manual = AdRequest.objects.filter(status=AdRequest.Status.PENDING_MANUAL).count()
    approved_today = AdRequest.objects.filter(
        status=AdRequest.Status.APPROVED,
        approved_at__gte=today_start,
    ).count()
    rejected_today = AdRequest.objects.filter(
        status=AdRequest.Status.REJECTED,
        updated_at__gte=today_start,
    ).count()

    decided_today = approved_today + rejected_today
    rejection_rate = (rejected_today / decided_today * 100) if decided_today else 0
    backlog = pending_ai + pending_manual
    pulse_score = 100
    if rejection_rate > 50:
        pulse_score -= min(50, (rejection_rate - 50) * 0.5)
    if backlog > 20:
        pulse_score -= min(50, (backlog - 20) * 0.5)
    pulse_score = max(0, min(100, round(pulse_score)))

    return {
        "total": total,
        "pending_ai": pending_ai,
        "pending_manual": pending_manual,
        "approved_today": approved_today,
        "rejected_today": rejected_today,
        "rejection_rate": round(rejection_rate, 1),
        "pulse_score": pulse_score,
        "system_health": "healthy" if pulse_score >= 60 else "stressed",
    }


def get_dashboard_context():
    """
    Full context for dashboard home: pulse data, home stats (active ads, templates,
    channels, API status), recent ads feed, last_edited_template for quick link.
    """
    pulse = get_pulse_data()
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    last_7_days = []
    for i in range(6, -1, -1):
        day = today_start - timedelta(days=i)
        day_end = day + timedelta(days=1)
        count = AdRequest.objects.filter(
            created_at__gte=day,
            created_at__lt=day_end,
        ).count()
        last_7_days.append({"date": day.strftime("%a"), "count": count})

    # Home stats: active ads (in workflow, not rejected/expired), templates, channels
    total_active_ads = AdRequest.objects.exclude(
        status__in=[AdRequest.Status.REJECTED, AdRequest.Status.EXPIRED]
    ).count()
    total_templates = AdTemplate.objects.filter(is_active=True).count()
    active_telegram_channels = TelegramChannel.objects.filter(is_active=True).count()

    config = SiteConfiguration.get_config()
    api_configured = bool((getattr(config, "openai_api_key", None) or "").strip())
    api_status = "OK" if api_configured else "Not configured"
    api_status_class = "ok" if api_configured else "not-configured"

    recent_ads = list(
        AdRequest.objects.select_related("category")
        .order_by("-created_at")[:5]
    )
    last_edited_template = AdTemplate.objects.order_by("-updated_at").first()

    return {
        **pulse,
        "last_7_days": last_7_days,
        "last_7_days_json": json.dumps(last_7_days),
        "total_active_ads": total_active_ads,
        "total_templates": total_templates,
        "active_telegram_channels": active_telegram_channels,
        "api_status": api_status,
        "api_status_class": api_status_class,
        "recent_ads": recent_ads,
        "last_edited_template": last_edited_template,
    }
