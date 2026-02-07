"""
Iraniu — Dynamic image generation for Instagram posts.
Creates branded images with message, email, phone overlay.
Meets Instagram requirements: min 320px width, max 1080px, aspect 1:1 to 4:5.
"""

import io
import logging
import os
import uuid
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Instagram: min 320, max 1080 per side; square (1:1) or portrait (4:5)
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


def generate_instagram_image(
    message: str,
    email: str = '',
    phone: str = '',
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    bg_color: tuple[int, int, int] = (255, 255, 255),
    text_color: tuple[int, int, int] = (33, 37, 41),
    accent_color: tuple[int, int, int] = (13, 110, 253),
    lang: str = 'en',
) -> bytes | None:
    """
    Generate a branded image with message, email, phone overlay.
    Returns PNG bytes or None if Pillow unavailable.
    """
    Image, ImageDraw, ImageFont = _ensure_pillow()
    if not Image:
        return None

    width = max(INSTAGRAM_MIN_SIZE, min(INSTAGRAM_MAX_SIZE, width))
    height = max(INSTAGRAM_MIN_SIZE, min(INSTAGRAM_MAX_SIZE, height))

    img = Image.new('RGB', (width, height), bg_color)
    draw = ImageDraw.Draw(img)

    font_paths_large = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        'C:/Windows/Fonts/arialbd.ttf',
        'arialbd.ttf',
    ]
    font_paths_small = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        'C:/Windows/Fonts/arial.ttf',
        'arial.ttf',
    ]
    font_large = font_small = ImageFont.load_default()
    for path in font_paths_large:
        try:
            font_large = ImageFont.truetype(path, 48)
            break
        except OSError:
            continue
    for path in font_paths_small:
        try:
            font_small = ImageFont.truetype(path, 32)
            break
        except OSError:
            continue

    padding = 60
    y = padding

    def _wrap_text(text: str, font, max_width: int) -> list[str]:
        lines = []
        for paragraph in (text or '').split('\n'):
            words = paragraph.split()
            current = []
            for w in words:
                test = ' '.join(current + [w])
                bbox = draw.textbbox((0, 0), test, font=font)
                if bbox[2] - bbox[0] <= max_width:
                    current.append(w)
                else:
                    if current:
                        lines.append(' '.join(current))
                    current = [w]
            if current:
                lines.append(' '.join(current))
        return lines

    max_text_width = width - 2 * padding

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
