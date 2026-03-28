"""
Normalize phone numbers for JSON export (outbound webhook, Partner API).
Single source of truth: same resolution as DeliveryService caption + E.164-style +prefix.
"""

from __future__ import annotations

from core.models import AdRequest

# E.164 subscriber number: max 15 digits (ITU-T E.164)
E164_MAX_DIGITS = 15


def resolve_ad_request_phone(ad: AdRequest) -> str:
    """
    Contact phone for an ad: model field, then contact_snapshot, then linked Telegram user.
    Raw string as stored (may include spaces); not normalized to E.164.
    """
    phone = (getattr(ad, "phone_number", None) or "").strip()
    contact = getattr(ad, "contact_snapshot", None) or {}
    if not phone and isinstance(contact, dict):
        phone = (contact.get("phone") or "").strip()
    if not phone and getattr(ad, "user_id", None) and ad.user:
        phone = (ad.user.phone_number or "").strip()
    return phone


def format_e164_plus_from_raw(raw: str) -> str:
    """
    Export form: leading '+' plus digits only (country code included), max 15 digits.
    Empty input or no digits → ''.
    """
    if not raw or not isinstance(raw, str):
        return ""
    digits = "".join(c for c in raw.strip() if c.isdigit())
    if not digits:
        return ""
    if len(digits) > E164_MAX_DIGITS:
        digits = digits[:E164_MAX_DIGITS]
    return "+" + digits


def format_ad_phone_e164_export(ad: AdRequest) -> str:
    """phone_number field for webhook and /api/v1/* JSON responses."""
    return format_e164_plus_from_raw(resolve_ad_request_phone(ad))
