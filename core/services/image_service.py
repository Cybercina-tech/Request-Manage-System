"""
Ad image generation from AdTemplate.
Uses Pillow to overlay category, ad text, and phone on the template background.
"""

import io
import logging
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)


def _ensure_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow not installed; run: pip install Pillow")
        return None, None, None


def _hex_to_rgb(hex_color: str):
    """Convert hex string (#RGB or #RRGGBB) to (r, g, b) tuple."""
    hex_color = (hex_color or "#FFFFFF").strip()
    if hex_color.startswith("#"):
        hex_color = hex_color[1:]
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    try:
        return (
            int(hex_color[0:2], 16),
            int(hex_color[2:4], 16),
            int(hex_color[4:6], 16),
        )
    except (ValueError, IndexError):
        return (255, 255, 255)


def _persian_font_path():
    """Path to banner font (Yekan.ttf) for category and message text."""
    base = Path(settings.BASE_DIR)
    for rel in ["static/fonts/Yekan.ttf", "Yekan.ttf", "static/fonts/Persian.ttf", "Persian.ttf"]:
        p = base / rel
        if p.exists():
            return str(p)
    return None


def _load_persian_font(ImageFont, size: int):
    """Load Yekan.ttf (banner font) for category and ad text (Farsi). Fallback: Persian, Vazir, Samim."""
    path = _persian_font_path()
    if path:
        return ImageFont.truetype(path, size)
    for rel in ["static/fonts/Vazir.ttf", "static/fonts/Samim.ttf"]:
        p = Path(settings.BASE_DIR) / rel
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _load_english_font(ImageFont, size: int):
    """Load an English/Latin font for phone numbers."""
    import platform
    base = Path(settings.BASE_DIR)
    media_root = getattr(settings, "MEDIA_ROOT", base / "media")
    search = [
        base / "static" / "fonts" / "English.ttf",
        base / "static" / "fonts" / "Roboto.ttf",
        Path(media_root) / "ad_templates" / "fonts" / "English.ttf",
        Path(media_root) / "ad_templates" / "fonts" / "Roboto.ttf",
    ]
    if platform.system() == "Windows":
        win_fonts = Path("C:/Windows/Fonts")
        for name in ["arial.ttf", "segoeui.ttf"]:
            search.append(win_fonts / name)
    for p in search:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _load_font(template_obj, ImageFont, size: int):
    """Load font from template's font_file, or fallback to Persian for Farsi text."""
    if getattr(template_obj, "font_file", None):
        try:
            path = template_obj.font_file.path
            if Path(path).exists():
                return ImageFont.truetype(path, size)
        except (OSError, ValueError, AttributeError) as e:
            logger.warning("Could not load template font: %s", e)
    return _load_persian_font(ImageFont, size)


def _wrap_text(draw, text: str, font, max_width: int) -> list:
    """Split text into lines that fit within max_width (word wrap)."""
    if max_width <= 0:
        return [text] if text else []
    lines = []
    for paragraph in (text or "").split("\n"):
        words = paragraph.split()
        current = []
        for w in words:
            test = " ".join(current + [w])
            bbox = draw.textbbox((0, 0), test, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current.append(w)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [w]
        if current:
            lines.append(" ".join(current))
    return lines


def generate_ad_image(template_obj, category_text: str, ad_text: str, phone_number: str):
    """
    Generate an ad image from an AdTemplate.

    1. Load the template's background image.
    2. Load the template's font (or fallback).
    3. Draw Category (heading), Ad Text (with wrapping), and Phone at their coordinates.
    4. Return a Django ContentFile (PNG bytes) for saving or streaming.

    Args:
        template_obj: AdTemplate instance (must have background_image set).
        category_text: Text for the category/heading.
        ad_text: Body text (will be word-wrapped by ad_text_max_width).
        phone_number: Phone number to draw.

    Returns:
        django.core.files.base.ContentFile containing PNG bytes, or None on failure.
    """
    Image, ImageDraw, ImageFont = _ensure_pillow()
    if not Image:
        return None

    # Use template background or default (static/images/default_template/Template.png)
    default_bg_rel = "static/images/default_template/Template.png"
    try:
        if template_obj.background_image:
            try:
                with template_obj.background_image.open("rb") as fh:
                    img = Image.open(fh).convert("RGB")
            except Exception as e:
                logger.warning("Could not load AdTemplate background: %s", e)
                img = None
        else:
            img = None
        if img is None:
            default_bg = Path(settings.BASE_DIR) / default_bg_rel
            if default_bg.exists():
                img = Image.open(default_bg).convert("RGB")
        if img is None:
            logger.warning("AdTemplate %s has no background_image and default not found", template_obj.pk)
            return None
    except Exception as e:
        logger.warning("Could not load AdTemplate background: %s", e)
        return None

    draw = ImageDraw.Draw(img)

    # Category and message: Persian font (Persian.ttf or template font_file)
    font_category = _load_font(template_obj, ImageFont, template_obj.category_font_size)
    font_ad_text = _load_font(template_obj, ImageFont, template_obj.ad_text_font_size)
    # Phone: always English/Latin font for numbers
    font_phone = _load_english_font(ImageFont, template_obj.phone_font_size)

    color_cat = _hex_to_rgb(template_obj.category_text_color)
    color_ad = _hex_to_rgb(template_obj.ad_text_text_color)
    color_phone = _hex_to_rgb(template_obj.phone_text_color)

    x_cat = template_obj.category_x_pos
    y_cat = template_obj.category_y_pos
    x_ad = template_obj.ad_text_x_pos
    y_ad = template_obj.ad_text_y_pos
    x_phone = template_obj.phone_x_pos
    y_phone = template_obj.phone_y_pos

    # 1. Category (heading)
    if category_text:
        draw.text((x_cat, y_cat), category_text[:200], fill=color_cat, font=font_category)

    # 2. Ad text with wrapping
    max_w = template_obj.ad_text_max_width or 800
    for line in _wrap_text(draw, ad_text or "", font_ad_text, max_w):
        draw.text((x_ad, y_ad), line[:500], fill=color_ad, font=font_ad_text)
        bbox = draw.textbbox((0, 0), line, font=font_ad_text)
        y_ad += bbox[3] - bbox[1] + 6

    # 3. Phone number
    if phone_number:
        draw.text((x_phone, y_phone), phone_number[:60], fill=color_phone, font=font_phone)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG", optimize=True)
    buffer.seek(0)
    return ContentFile(buffer.read(), name="ad_preview.png")
