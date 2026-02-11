"""Utilities for recording activity logs."""

from __future__ import annotations

from typing import Any

from core.models import ActivityLog


def log_activity(*, user=None, action: str, object_type: str = "", object_repr: str = "", metadata: dict[str, Any] | None = None) -> None:
    """Create an activity log row. Fail-safe by design."""
    try:
        ActivityLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            action=(action or "").strip()[:128] or "action",
            object_type=(object_type or "").strip()[:64],
            object_repr=(object_repr or "").strip()[:255],
            metadata=metadata or {},
        )
    except Exception:
        # Never break request flow due to logging
        pass

