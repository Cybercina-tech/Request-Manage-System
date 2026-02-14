"""
Iraniu — Dynamic image generation for Instagram posts.

Uses static/banner_config.json for font path and colors.
Font: static/fonts/YekanBakh-Bold.ttf. Raw text (no arabic_reshaper/python-bidi).
Canvas: 1080x1080 (Square). Meets Instagram: min 320px, max 1080px.
"""

import io
import json
import logging
import os
import uuid
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

INSTAGRAM_MIN_SIZE = 320
INSTAGRAM_MAX_SIZE = 1080
DEFAULT_WIDTH = 1080
DEFAULT_HEIGHT = 1080


def _ensure_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow not installed; run: pip install Pillow")
        return None, None, None


def _load_banner_config() -> dict | None:
    """Load static/banner_config.json. Returns None if missing or invalid."""
    config_path = Path(settings.BASE_DIR) / "static" / "banner_config.json"
    if not config_path.exists():
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.debug("instagram_image: could not load banner_config: %s", e)
        return None


def _hex_to_rgb(value: str):
    value = (value or "#FFFFFF").strip()
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except (ValueError, IndexError):
        return (255, 255, 255)


def _yekan_bakh_path() -> Path | None:
    base = Path(settings.BASE_DIR)
    for p in [base / "static" / "fonts" / "YekanBakh-Bold.ttf", base / "YekanBakh-Bold.ttf"]:
        if p.exists():
            return p
    return None


def _load_banner_font(ImageFont, size: int):
    """Load YekanBakh-Bold.ttf. No fake bold."""
    path = _yekan_bakh_path()
    if path:
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def generate_instagram_image(
    message: str,
    email: str = '',
    phone: str = '',
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    bg_color: tuple[int, int, int] | None = None,
    text_color: tuple[int, int, int] | None = None,
    accent_color: tuple[int, int, int] | None = None,
    lang: str = 'en',
) -> bytes | None:
    """
    Generate a branded image with message, email, phone overlay.

    Font and colors from banner_config.json; font: YekanBakh-Bold.ttf; raw text.
    Returns PNG bytes or None if Pillow unavailable.
    """
    Image, ImageDraw, ImageFont = _ensure_pillow()
    if not Image:
        return None

    config = _load_banner_config() or {}
    msg_conf = config.get("message", config.get("description", {}))
    cat_conf = config.get("category", {})
    # Colors from config; fallbacks only when config missing
    if text_color is None:
        text_color = _hex_to_rgb(msg_conf.get("color") or "#FFFFFF")
    if accent_color is None:
        accent_color = _hex_to_rgb(cat_conf.get("color") or "#EEFF00")
    if bg_color is None:
        bg_color = (28, 28, 38)

    width = max(INSTAGRAM_MIN_SIZE, min(INSTAGRAM_MAX_SIZE, width))
    height = max(INSTAGRAM_MIN_SIZE, min(INSTAGRAM_MAX_SIZE, height))

    img = Image.new('RGB', (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    size_large = int(msg_conf.get("size", 58)) + 10
    size_small = int(msg_conf.get("size", 58))
    font_large = _load_banner_font(ImageFont, min(size_large, 72))
    font_small = _load_banner_font(ImageFont, size_small)

    padding = 60
    y = padding
    max_text_width = width - 2 * padding

    def _wrap_text(text: str, font, max_w: int) -> list[str]:
        lines = []
        for paragraph in (text or '').split('\n'):
            words = paragraph.split()
            current = []
            for w in words:
                test = ' '.join(current + [w])
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] <= max_w:
                    current.append(w)
                else:
                    if current:
                        lines.append(' '.join(current))
                    current = [w]
            if current:
                lines.append(' '.join(current))
        return lines

    brand = 'Iraniu' if lang == 'en' else 'ایرانيو'
    draw.text((padding, y), brand, fill=accent_color, font=font_large)
    bbox = draw.textbbox((0, 0), brand, font=font_large)
    y += bbox[3] - bbox[1] + 20

    for line in _wrap_text(message, font_small, max_text_width):
        draw.text((padding, y), line[:200], fill=text_color, font=font_small)
        bbox = draw.textbbox((0, 0), line, font=font_small)
        y += bbox[3] - bbox[1] + 8

    if email or phone:
        y += 24
        if email:
            contact = f'📧 {email}' if lang == 'en' else f'ایمیل: {email}'
            draw.text((padding, y), contact[:100], fill=text_color, font=font_small)
            bbox = draw.textbbox((0, 0), contact, font=font_small)
            y += bbox[3] - bbox[1] + 8
        if phone:
            contact = f'📞 {phone}' if lang == 'en' else f'تلفن: {phone}'
            draw.text((padding, y), contact[:100], fill=text_color, font=font_small)
            y += 40

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def save_generated_image(
    message: str,
    email: str = '',
    phone: str = '',
    lang: str = 'en',
    subdir: str = 'instagram',
) -> str | None:
    """
    Generate image, save to MEDIA_ROOT, return relative URL for posting.
    Caller must ensure MEDIA_URL is publicly accessible for Instagram.
    """
    Image, _, _ = _ensure_pillow()
    if not Image:
        return None

    media_root = getattr(settings, 'MEDIA_ROOT', None)
    media_url = getattr(settings, 'MEDIA_URL', '/media/')
    if not media_root:
        media_root = Path(settings.BASE_DIR) / 'media'
    base = Path(media_root) / subdir
    base.mkdir(parents=True, exist_ok=True)
    name = f'{uuid.uuid4().hex[:12]}.png'
    path = base / name

    data = generate_instagram_image(message=message, email=email, phone=phone, lang=lang)
    if not data:
        return None

    with open(path, 'wb') as f:
        f.write(data)

    rel = str(Path(subdir) / name).replace('\\', '/')
    if not media_url.endswith('/'):
        media_url += '/'
    return f'{media_url}{rel}'


def get_absolute_media_url(relative_or_media_url: str, request=None) -> str:
    """
    Convert MEDIA_URL-relative path to absolute URL for Instagram.
    Instagram requires publicly accessible image URLs.
    """
    url = relative_or_media_url or ''
    if url.startswith('http://') or url.startswith('https://'):
        return url
    base = getattr(settings, 'INSTAGRAM_BASE_URL', None) or ''
    if not base and request:
        base = request.build_absolute_uri('/').rstrip('/')
    if not base:
        base = os.environ.get('INSTAGRAM_BASE_URL', 'https://example.com')
    if url.startswith('/'):
        return base + url
    media = getattr(settings, 'MEDIA_URL', '/media/')
    if not media.startswith('/'):
        media = '/' + media
    return base + media.rstrip('/') + '/' + url.lstrip('/')
