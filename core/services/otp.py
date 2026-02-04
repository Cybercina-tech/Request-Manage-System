"""
Iranio — OTP generation and verification (hashed codes).
Active only when ENABLE_OTP is True. No sending logic.
"""

import hashlib
import secrets
import logging
from datetime import timedelta
from django.utils import timezone

from core.conf import ENABLE_OTP
from core.models import TelegramUser, VerificationCode

logger = logging.getLogger(__name__)

CODE_LENGTH = 6
DEFAULT_EXPIRY_MINUTES = 10


def hash_code(plain: str) -> str:
    """Hash code for storage. Never store plain."""
    return hashlib.sha256(plain.encode()).hexdigest()


def generate_code(
    user: TelegramUser,
    channel: str,
    expiry_minutes: int = DEFAULT_EXPIRY_MINUTES,
) -> str | None:
    """
    Generate 6-digit code, store hashed in VerificationCode.
    Returns plain code only when ENABLE_OTP is True; otherwise None.
    Caller must send code (not implemented here).
    """
    if not ENABLE_OTP:
        return None
    if channel not in (VerificationCode.Channel.EMAIL, VerificationCode.Channel.PHONE):
        return None
    plain = "".join(secrets.choice("0123456789") for _ in range(CODE_LENGTH))
    expires_at = timezone.now() + timedelta(minutes=expiry_minutes)
    VerificationCode.objects.create(
        user=user,
        channel=channel,
        code_hashed=hash_code(plain),
        expires_at=expires_at,
    )
    return plain


def verify_code(user: TelegramUser, channel: str, plain: str) -> bool:
    """
    Verify code: must match latest unused, non-expired VerificationCode.
    Marks code as used on success. Returns False when ENABLE_OTP is False.
    """
    if not ENABLE_OTP:
        return False
    now = timezone.now()
    qs = (
        VerificationCode.objects.filter(
            user=user,
            channel=channel,
            used=False,
            expires_at__gt=now,
        )
        .order_by("-created_at")
    )
    for vc in qs[:1]:
        if vc.code_hashed == hash_code(plain):
            vc.used = True
            vc.save(update_fields=["used"])
            return True
    return False
