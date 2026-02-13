"""
Iraniu — Validators for AdRequest content.
Enforces 80-char limit and Persian-only (letters, numbers, punctuation).
"""

import re

from django.core.exceptions import ValidationError

AD_CONTENT_MAX_LENGTH = 80

# Latin letters (a-z, A-Z) — if present, content is invalid (must be Persian-only)
LATIN_LETTER_PATTERN = re.compile(r"[a-zA-Z]")


def validate_ad_content_length(value: str) -> None:
    """
    Ensure ad content does not exceed 80 characters (including spaces).
    """
    if not value:
        return
    if len(value) > AD_CONTENT_MAX_LENGTH:
        raise ValidationError(
            "متن آگهی شما بیش از حد طولانی است. حداکثر مجاز: ۸۰ کاراکتر.",
            code="ad_content_too_long",
        )


def validate_ad_content_persian(value: str) -> None:
    """
    Ensure ad content uses only Persian letters, numbers (Persian/English),
    and standard punctuation. Rejects Latin letters.
    """
    if not value or not isinstance(value, str):
        return
    if LATIN_LETTER_PATTERN.search(value):
        raise ValidationError(
            "لطفاً آگهی خود را فقط به زبان فارسی بنویسید.",
            code="ad_content_not_persian",
        )


def validate_ad_content(value: str) -> None:
    """
    Run all ad content validations: length (80 chars) and Persian-only.
    Raises ValidationError with appropriate message on failure.
    """
    validate_ad_content_length(value)
    validate_ad_content_persian(value)


def validate_ad_content_with_feedback(text: str) -> tuple[bool, str | None]:
    """
    Validate ad content and return (is_valid, error_message).
    Use in bot/conversation flow to show localized errors via i18n keys.
    Returns (False, "ad_content_too_long") or (False, "ad_content_not_persian") or (True, None).
    """
    if not text or not isinstance(text, str):
        return True, None
    try:
        validate_ad_content(text)
        return True, None
    except ValidationError as e:
        code = getattr(e, "code", None) or (e.error_list[0].code if e.error_list else None)
        if code == "ad_content_too_long":
            return False, "ad_content_too_long"
        if code == "ad_content_not_persian":
            return False, "ad_content_not_persian"
        return False, "ad_content_not_persian"  # fallback
