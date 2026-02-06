"""
Iraniu — Dashboard and pulse data. Single source of truth for KPIs and health.
"""

import json
from datetime import timedelta
from django.utils import timezone

from core.models import AdRequest


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
    Full context for dashboard template: pulse data + last_7_days chart.
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

    return {
        **pulse,
        "last_7_days": last_7_days,
        "last_7_days_json": json.dumps(last_7_days),
    }
