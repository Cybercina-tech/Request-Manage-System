"""Validation helpers for forms/views that handle user-provided files and coordinates."""

from __future__ import annotations

import re
from typing import Any

from django.core.exceptions import ValidationError

HEX_COLOR_RE = re.compile(r"^#(?:[0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")


def parse_int_in_range(
    raw_value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    """Parse integer value and enforce inclusive numeric bounds."""
    try:
        value = int(raw_value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field_name} must be a valid integer.") from exc
    if value < minimum or value > maximum:
        raise ValidationError(f"{field_name} must be between {minimum} and {maximum}.")
    return value


def parse_hex_color(raw_value: Any, *, field_name: str, default: str) -> str:
    """Normalize and validate short/long hex colors (#RGB or #RRGGBB)."""
    value = (str(raw_value).strip() if raw_value is not None else "") or default
    if not HEX_COLOR_RE.fullmatch(value):
        raise ValidationError(f"{field_name} must be a valid hex color.")
    if len(value) == 4:
        return "#" + "".join(ch * 2 for ch in value[1:])
    return value.upper()


def validate_uploaded_image(
    uploaded_file: Any,
    *,
    max_size_bytes: int,
    field_name: str = "Image",
) -> None:
    """Validate image MIME type, file size, and binary integrity."""
    if not uploaded_file:
        return
    if getattr(uploaded_file, "size", 0) > max_size_bytes:
        mb = max_size_bytes / (1024 * 1024)
        raise ValidationError(f"{field_name} size must be <= {mb:.1f}MB.")

    content_type = (getattr(uploaded_file, "content_type", "") or "").lower()
    if not content_type.startswith("image/"):
        raise ValidationError(f"{field_name} must be an image file.")

    try:
        from PIL import Image

        uploaded_file.seek(0)
        with Image.open(uploaded_file) as img:
            img.verify()
    except Exception as exc:
        raise ValidationError(f"{field_name} is not a valid image.") from exc
    finally:
        try:
            uploaded_file.seek(0)
        except Exception:
            pass

