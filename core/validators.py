"""
Iraniu — Validators for AdRequest content.
Enforces max length; optional Persian-only check kept for rare reuse (not on model by default).
"""

import re

from django.core.exceptions import ValidationError

AD_CONTENT_MAX_LENGTH = 500

# Latin letters (a-z, A-Z) — if present, content is invalid (must be Persian-only)
LATIN_LETTER_PATTERN = re.compile(r"[a-zA-Z]")


def validate_ad_content_length(value: str) -> None:
    """
    Ensure ad content does not exceed AD_CONTENT_MAX_LENGTH characters (including spaces).
    """
    if not value:
        return
    if len(value) > AD_CONTENT_MAX_LENGTH:
        raise ValidationError(
            "متن آگهی شما بیش از حد طولانی است. حداکثر مجاز: ۵۰۰ کاراکتر.",
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
    Run ad content validations used for storage/API: length only.
    Raises ValidationError on failure.
    """
    validate_ad_content_length(value)


def validate_ad_content_with_feedback(text: str) -> tuple[bool, str | None]:
    """
    Validate ad content and return (is_valid, error_message).
    Use in bot/conversation flow to show localized errors via i18n keys.
    Returns (False, "ad_content_too_long") or (True, None).
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
        return False, "ad_content_too_long"
