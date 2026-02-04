"""
Iranio — Telegram user profile: get_or_create from update, update contact info.
No logic in views; all here.
"""

import re
import logging
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.core.validators import validate_email as django_validate_email

from core.models import TelegramUser

logger = logging.getLogger(__name__)

# E.164: optional +, digits only, max 15
E164_PATTERN = re.compile(r"^\+?[0-9]{10,15}$")


def _extract_from_user(update: dict) -> dict | None:
    """Extract 'from' user dict from Telegram update (message or callback_query)."""
    msg = update.get("message")
    if msg and "from" in msg:
        return msg["from"]
    cb = update.get("callback_query")
    if cb and "from" in cb:
        return cb["from"]
    return None


def get_or_create_user_from_update(update: dict) -> TelegramUser | None:
    """
    Create or update TelegramUser from webhook update.
    Update fields if changed; update last_seen.
    Call before any conversation logic.
    """
    from_user = _extract_from_user(update)
    if not from_user:
        return None

    telegram_user_id = from_user.get("id")
    if telegram_user_id is None:
        return None

    try:
        telegram_user_id = int(telegram_user_id)
    except (TypeError, ValueError):
        return None

    username = (from_user.get("username") or "").strip() or None
    if username and len(username) > 128:
        username = username[:128]
    first_name = (from_user.get("first_name") or "").strip() or None
    if first_name and len(first_name) > 128:
        first_name = first_name[:128]
    last_name = (from_user.get("last_name") or "").strip() or None
    if last_name and len(last_name) > 128:
        last_name = last_name[:128]
    language_code = (from_user.get("language_code") or "").strip() or None
    if language_code and len(language_code) > 8:
        language_code = language_code[:8]
    is_bot = bool(from_user.get("is_bot"))

    now = timezone.now()
    user, created = TelegramUser.objects.get_or_create(
        telegram_user_id=telegram_user_id,
        defaults={
            "username": username,
            "first_name": first_name,
            "last_name": last_name,
            "language_code": language_code,
            "is_bot": is_bot,
            "last_seen": now,
        },
    )
    if not created:
        updated = False
        if user.username != username:
            user.username = username
            updated = True
        if user.first_name != first_name:
            user.first_name = first_name
            updated = True
        if user.last_name != last_name:
            user.last_name = last_name
            updated = True
        if user.language_code != language_code:
            user.language_code = language_code
            updated = True
        if user.is_bot != is_bot:
            user.is_bot = is_bot
            updated = True
        user.last_seen = now
        if updated:
            user.save(update_fields=["username", "first_name", "last_name", "language_code", "is_bot", "last_seen"])
        else:
            user.save(update_fields=["last_seen"])

    return user


# Spec alias: get_or_create_telegram_user(update)
get_or_create_telegram_user = get_or_create_user_from_update


def validate_phone(value: str) -> str:
    """
    Validate E.164 phone. Max 15 digits.
    Returns normalized value (e.g. +989123456789) or raises ValueError.
    """
    if not value or not isinstance(value, str):
        raise ValueError("Phone is required")
    cleaned = re.sub(r"\s", "", value.strip())
    if not E164_PATTERN.match(cleaned):
        raise ValueError("Invalid phone format (use E.164, e.g. +989123456789)")
    digits_only = re.sub(r"\D", "", cleaned)
    if len(digits_only) > 15:
        raise ValueError("Phone must be at most 15 digits")
    return "+" + digits_only


def validate_email(value: str) -> str:
    """Validate email with Django EmailValidator. Returns normalized value or raises ValueError."""
    if not value or not isinstance(value, str):
        raise ValueError("Email is required")
    cleaned = value.strip().lower()
    try:
        django_validate_email(cleaned)
    except ValidationError as e:
        raise ValueError(e.messages[0] if e.messages else "Invalid email format")
    return cleaned


def update_contact_info(
    telegram_user: TelegramUser,
    *,
    phone: str | None = None,
    email: str | None = None,
    mark_phone_verified: bool = False,
    mark_email_verified: bool = False,
) -> None:
    """
    Save phone/email to TelegramUser. Mark unverified unless explicitly set.
    Validates format; does not log contact info.
    """
    update_fields = []
    if phone is not None:
        telegram_user.phone_number = validate_phone(phone)
        telegram_user.phone_verified = mark_phone_verified
        update_fields.extend(["phone_number", "phone_verified"])
    if email is not None:
        telegram_user.email = validate_email(email)
        telegram_user.email_verified = mark_email_verified
        update_fields.extend(["email", "email_verified"])
    if update_fields:
        telegram_user.save(update_fields=update_fields)
