"""
Ad image generation from AdTemplate.

Delegates to core.services.image_engine.create_ad_image so all styling
(font: YekanBakh-Bold.ttf, coordinates/colors from banner_config.json, raw text)
is consistent. Returns a Django ContentFile for saving or streaming.
"""

import logging
from pathlib import Path

from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


def generate_ad_image(template_obj, category_text: str, ad_text: str, phone_number: str):
    """
    Generate an ad image from an AdTemplate.

    Uses image_engine.create_ad_image: banner_config.json for coordinates/font/colors,
    static/fonts/YekanBakh-Bold.ttf, raw text (no arabic_reshaper/python-bidi).
    Returns a Django ContentFile (PNG bytes) for saving or streaming.

    Args:
        template_obj: AdTemplate instance (must be saved, with or without background_image).
        category_text: Text for the category/heading.
        ad_text: Body text (word-wrapped by config max_width).
        phone_number: Phone number to draw.

    Returns:
        django.core.files.base.ContentFile containing PNG bytes, or None on failure.
    """
    from core.services.image_engine import create_ad_image
    import tempfile
    import os

    try:
        pk = template_obj.pk
    except Exception:
        logger.warning("image_service.generate_ad_image: template has no pk")
        return None

    fd, out_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        path = create_ad_image(
            pk,
            category_text or "",
            ad_text or "",
            phone_number or "",
            format_type="POST",
            output_path=out_path,
        )
        if path and Path(path).exists():
            with open(path, "rb") as f:
                return ContentFile(f.read(), name="ad_preview.png")
    except Exception as e:
        logger.warning("image_service.generate_ad_image: %s", e)
    finally:
        Path(out_path).unlink(missing_ok=True)
    return None
