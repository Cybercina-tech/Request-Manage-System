"""
Iraniu — Image generation for Instagram posts and stories from AdRequest.
Loads background template, overlays title, description, and verified phone.
Uses Persian fonts (Vazir/Samim) when available.
"""

import logging
import uuid
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

# Instagram: Feed 1:1 or 4:5; Story 9:16 (1080x1920)
FEED_WIDTH = 1080
FEED_HEIGHT = 1080
STORY_WIDTH = 1080
STORY_HEIGHT = 1920

# Fallback background paths (template or generated solid)
TEMPLATE_PATHS = [
    'static/images/insta_bg.jpg',
    'static/images/insta_bg.png',
]


def _ensure_pillow():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow not installed; run: pip install Pillow")
        return None, None, None


def _find_font(ImageFont, size: int, prefer_persian: bool = True):
    """Find best font: Vazir/Samim for Persian, else DejaVu/Arial."""
    persian_paths = [
        '/usr/share/fonts/truetype/vazir/Vazir.ttf',
        '/usr/share/fonts/truetype/vazir/Vazir-Bold.ttf',
        '/usr/share/fonts/truetype/samim/Samim.ttf',
        '/usr/share/fonts/truetype/samim/Samim-Bold.ttf',
        str(Path(settings.BASE_DIR) / 'static' / 'fonts' / 'Vazir.ttf'),
        str(Path(settings.BASE_DIR) / 'static' / 'fonts' / 'Samim.ttf'),
    ]
    fallback_paths = [
        '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf',
        'C:/Windows/Fonts/arial.ttf',
        'C:/Windows/Fonts/arialbd.ttf',
        'arial.ttf',
    ]
    paths = persian_paths + fallback_paths if prefer_persian else fallback_paths + persian_paths
    for path in paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _load_background(width: int, height: int, base_dir: Path) -> 'Image.Image | None':
    """Load background from template or create solid."""
    Image, _, _ = _ensure_pillow()
    if not Image:
        return None
    for rel in TEMPLATE_PATHS:
        path = base_dir / rel
        if path.exists():
            try:
                img = Image.open(path).convert('RGB')
                img = img.resize((width, height), Image.Resampling.LANCZOS)
                return img
            except Exception as e:
                logger.warning("Could not load template %s: %s", path, e)
    # Fallback: solid gradient-ish background
    img = Image.new('RGB', (width, height), (28, 28, 38))
    return img


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
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


def generate_request_image(request_id: int, is_story: bool = False) -> str | None:
    """
    Generate Instagram image from AdRequest.

    - Loads background from static/images/insta_bg.jpg (or creates solid)
    - Overlays category/title, content/description, verified phone
    - Uses Vazir/Samim for Persian text when available
    - Saves to MEDIA_ROOT/instagram/ and returns absolute filesystem path

    Args:
        request_id: AdRequest pk (primary key)
        is_story: If True, output 9:16 (Story); else 1:1 (Feed)

    Returns:
        Absolute path to saved image, or None on failure
    """
    Image, ImageDraw, ImageFont = _ensure_pillow()
    if not Image:
        return None

    from core.models import AdRequest

    try:
        ad = AdRequest.objects.select_related('category', 'user').get(pk=request_id)
    except AdRequest.DoesNotExist:
        logger.warning("generate_request_image: AdRequest pk=%s not found", request_id)
        return None

    width = STORY_WIDTH if is_story else FEED_WIDTH
    height = STORY_HEIGHT if is_story else FEED_HEIGHT
    base_dir = Path(settings.BASE_DIR)

    img = _load_background(width, height, base_dir)
    if not img:
        return None
    draw = ImageDraw.Draw(img)

    padding = 48 if is_story else 60
    max_text_width = width - 2 * padding
    font_title = _find_font(ImageFont, 52 if is_story else 48)
    font_body = _find_font(ImageFont, 36 if is_story else 32)

    text_color = (255, 255, 255)
    accent_color = (255, 200, 80)
    y = padding

    # Title: category name
    title = ad.get_category_display() if ad.category else 'آگهی'
    draw.text((padding, y), title[:80], fill=accent_color, font=font_title)
    bbox = draw.textbbox((0, 0), title, font=font_title)
    y += bbox[3] - bbox[1] + 20

    # Description: content
    content = (ad.content or '')[:1500]
    for line in _wrap_text(draw, content, font_body, max_text_width):
        draw.text((padding, y), line[:200], fill=text_color, font=font_body)
        bbox = draw.textbbox((0, 0), line, font=font_body)
        y += bbox[3] - bbox[1] + 8
        if y > height - 150:
            break

    # Phone (verified preferred)
    phone = ''
    contact = getattr(ad, 'contact_snapshot', None) or {}
    phone = (contact.get('phone') or '').strip()
    if not phone and ad.user_id:
        phone = (ad.user.phone_number or '').strip()
    if phone:
        y += 24
        label = f'📞 {phone}'
        draw.text((padding, y), label[:60], fill=text_color, font=font_body)
        y += 50

    # Branding
    brand = 'ایرانيو — Iraniu'
    draw.text((padding, height - padding - 40), brand, fill=(180, 180, 180), font=font_body)

    # Save to MEDIA_ROOT/instagram/
    media_root = getattr(settings, 'MEDIA_ROOT', base_dir / 'media') or (base_dir / 'media')
    out_dir = Path(media_root) / 'instagram'
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f'request_{request_id}_{uuid.uuid4().hex[:8]}.png'
    path = out_dir / name
    img.save(path, format='PNG', optimize=True)
    return str(path.resolve())
