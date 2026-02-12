"""
Iraniu — Dashboard and pulse data. Single source of truth for KPIs and health.
"""

import os
from datetime import timedelta
from django.utils import timezone
from django.db.models import Count, Q
from django.db.models.functions import TruncDate
from django.core.cache import cache

from core.models import AdRequest, AdTemplate, TelegramChannel, SiteConfiguration, ActivityLog, TelegramBot, InstagramConfiguration, SystemStatus


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
    Full context for dashboard analytics command center.
    """
    pulse = get_pulse_data()
    now = timezone.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Month-over-month totals
    this_month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    if this_month_start.month == 1:
        last_month_start = this_month_start.replace(year=this_month_start.year - 1, month=12)
    else:
        last_month_start = this_month_start.replace(month=this_month_start.month - 1)
    total_ads_this_month = AdRequest.objects.filter(created_at__gte=this_month_start).count()
    total_ads_last_month = AdRequest.objects.filter(
        created_at__gte=last_month_start,
        created_at__lt=this_month_start,
    ).count()
    if total_ads_last_month > 0:
        monthly_growth_pct = round(((total_ads_this_month - total_ads_last_month) / total_ads_last_month) * 100, 1)
    else:
        monthly_growth_pct = 100.0 if total_ads_this_month > 0 else 0.0

    # Weekly change badge for "Total Ads"
    this_week_start = today_start - timedelta(days=today_start.weekday())
    last_week_start = this_week_start - timedelta(days=7)
    this_week_total = AdRequest.objects.filter(created_at__gte=this_week_start).count()
    last_week_total = AdRequest.objects.filter(created_at__gte=last_week_start, created_at__lt=this_week_start).count()
    if last_week_total > 0:
        weekly_growth_pct = round(((this_week_total - last_week_total) / last_week_total) * 100, 1)
    else:
        weekly_growth_pct = 100.0 if this_week_total > 0 else 0.0

    # 7-day line chart via aggregation
    start_date = (today_start - timedelta(days=6)).date()
    ads_per_day_qs = (
        AdRequest.objects.filter(created_at__date__gte=start_date)
        .annotate(day=TruncDate('created_at'))
        .values('day')
        .annotate(total=Count('id'))
        .order_by('day')
    )
    ads_per_day_map = {row['day']: row['total'] for row in ads_per_day_qs}
    last_7_days = []
    for i in range(7):
        day = start_date + timedelta(days=i)
        last_7_days.append({"date": day.strftime("%a"), "count": ads_per_day_map.get(day, 0)})

    # Status distribution grouped into Draft / Published / Failed
    status_counts = AdRequest.objects.aggregate(
        draft=Count(
            'id',
            filter=Q(status__in=[AdRequest.Status.PENDING_AI, AdRequest.Status.PENDING_MANUAL, AdRequest.Status.NEEDS_REVISION]),
        ),
        published=Count('id', filter=Q(status__in=[AdRequest.Status.APPROVED, AdRequest.Status.SOLVED])),
        failed=Count('id', filter=Q(status__in=[AdRequest.Status.REJECTED, AdRequest.Status.EXPIRED])),
    )

    total_templates = AdTemplate.objects.filter(is_active=True).count()
    active_telegram_channels = TelegramChannel.objects.filter(is_active=True).count()
    active_bots = TelegramBot.objects.filter(is_active=True).exclude(bot_token_encrypted='').count()

    config = SiteConfiguration.get_config()
    openai_configured = bool(
        (os.environ.get("OPENAI_API_KEY") or "").strip()
        or (getattr(config, "openai_api_key", None) or "").strip()
    )
    instagram_configured = bool(
        getattr(config, 'is_instagram_enabled', False)
        or InstagramConfiguration.objects.filter(is_active=True).exclude(access_token_encrypted='').exists()
    )
    telegram_configured = active_bots > 0
    system_health_ok = openai_configured and telegram_configured and instagram_configured
    system_health_text = "APIs Connected" if system_health_ok else "Connection Error"
    system_health_class = "ok" if system_health_ok else "error"

    # System Health widget: worker, telegram, instagram, database
    try:
        status = SystemStatus.get_status()
        last_hb = status.last_heartbeat
        worker_online = last_hb is not None and (now - last_hb).total_seconds() <= 120
    except Exception:
        worker_online = False
    telegram_online = TelegramBot.objects.filter(
        is_active=True,
        status=TelegramBot.Status.ONLINE,
    ).exists() or TelegramBot.objects.filter(
        is_active=True,
        last_heartbeat__gte=now - timedelta(seconds=120),
    ).exists()
    instagram_valid_cached = cache.get("dashboard_instagram_valid")
    if instagram_valid_cached is None:
        try:
            from core.services.instagram import validate_instagram_token
            token = config.get_facebook_access_token() or ""
            if not token and InstagramConfiguration.objects.filter(is_active=True).exclude(access_token_encrypted="").exists():
                ig_config = InstagramConfiguration.objects.filter(is_active=True).exclude(access_token_encrypted="").first()
                token = ig_config.get_decrypted_token() if ig_config else ""
            instagram_valid_cached = bool(token and validate_instagram_token(token)[0])
            cache.set("dashboard_instagram_valid", instagram_valid_cached, timeout=300)
        except Exception:
            instagram_valid_cached = False
    try:
        from django.db import connection
        connection.ensure_connection()
        database_healthy = True
    except Exception:
        database_healthy = False

    recent_activities = list(ActivityLog.objects.select_related('user').order_by('-created_at')[:5])
    last_edited_template = AdTemplate.objects.order_by("-updated_at").first()

    return {
        **pulse,
        "last_7_days": last_7_days,
        "total_ads_this_month": total_ads_this_month,
        "total_ads_last_month": total_ads_last_month,
        "monthly_growth_pct": monthly_growth_pct,
        "weekly_growth_pct": weekly_growth_pct,
        "total_templates": total_templates,
        "active_telegram_channels": active_telegram_channels,
        "status_counts": status_counts,
        "status_distribution": [
            status_counts.get("published", 0),
            status_counts.get("draft", 0),
            status_counts.get("failed", 0),
        ],
        "system_health_ok": system_health_ok,
        "system_health_text": system_health_text,
        "system_health_class": system_health_class,
        "worker_online": worker_online,
        "telegram_online": telegram_online,
        "instagram_valid": instagram_valid_cached,
        "database_healthy": database_healthy,
        "recent_activities": recent_activities,
        "last_edited_template": last_edited_template,
    }
