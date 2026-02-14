"""
Iraniu — Image generation for Instagram posts and stories from AdRequest.

Uses static/banner_config.json for coordinates, font sizes, and colors.
Font: static/fonts/YekanBakh-Bold.ttf. Raw text to draw (no arabic_reshaper/python-bidi).
Canvas: Feed 1080x1350, Story 1080x1920.
"""

import json
import logging
import uuid
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

FEED_WIDTH = 1080
FEED_HEIGHT = 1350
STORY_WIDTH = 1080
STORY_HEIGHT = 1920
STORY_Y_OFFSET = 285

TEMPLATE_PATHS = [
    "static/images/insta_bg.jpg",
    "static/images/insta_bg.png",
]

YEKAN_BAKH_BOLD = "static/fonts/YekanBakh-Bold.ttf"


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
        if not isinstance(data, dict):
            return None
        if "message" in data and "description" not in data:
            data = dict(data)
            data["description"] = data.pop("message")
        return data
    except Exception as e:
        logger.debug("image_generator: could not load banner_config: %s", e)
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


def _resolve_font_path(rel_path: str | None) -> Path | None:
    """Resolve font_path from config against BASE_DIR. Fallback: YekanBakh-Bold.ttf."""
    base = Path(settings.BASE_DIR)
    if rel_path:
        p = (base / rel_path.replace("\\", "/")).resolve()
        if p.exists():
            return p
    p = base / "static" / "fonts" / "YekanBakh-Bold.ttf"
    return p if p.exists() else (base / "YekanBakh-Bold.ttf" if (base / "YekanBakh-Bold.ttf").exists() else None)


def _load_banner_font(ImageFont, size: int, font_path_override: str | None = None):
    """Load YekanBakh-Bold.ttf at given size (from config). No fake bold."""
    path = _resolve_font_path(font_path_override)
    if path:
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _load_english_font(ImageFont, size: int):
    """English/Latin font for phone numbers. Prefers monstrat.ttf."""
    base = Path(settings.BASE_DIR)
    media_root = getattr(settings, "MEDIA_ROOT", base / "media") or (base / "media")
    for p in [
        base / "static" / "fonts" / "monstrat.ttf",
        Path(media_root) / "ad_templates" / "fonts" / "monstrat.ttf",
        base / "static" / "fonts" / "English.ttf",
        base / "static" / "fonts" / "Roboto.ttf",
        Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]:
        if p.exists():
            try:
                return ImageFont.truetype(str(p), size)
            except OSError:
                continue
    return ImageFont.load_default()


def _load_background(width: int, height: int, base_dir: Path):
    Image, _, _ = _ensure_pillow()
    if not Image:
        return None
    for rel in TEMPLATE_PATHS:
        path = base_dir / rel
        if path.exists():
            try:
                img = Image.open(path).convert("RGB")
                return img.resize((width, height), Image.Resampling.LANCZOS)
            except Exception as e:
                logger.warning("Could not load template %s: %s", path, e)
    return Image.new("RGB", (width, height), (28, 28, 38))


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
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


def _coerce_int(value, default: int, min_val: int | None = None, max_val: int | None = None) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = default
    if min_val is not None:
        n = max(min_val, n)
    if max_val is not None:
        n = min(max_val, n)
    return n


def generate_request_image(request_id: int, is_story: bool = False) -> str | None:
    """
    Generate Instagram image from AdRequest.

    Uses banner_config.json for coordinates, font sizes, colors.
    Font: YekanBakh-Bold.ttf. Raw text (no arabic_reshaper/python-bidi).
    Feed: 1080x1350; Story: 1080x1920.
    Saves to MEDIA_ROOT/instagram/ and returns absolute path.
    """
    Image, ImageDraw, ImageFont = _ensure_pillow()
    if not Image:
        return None

    from core.models import AdRequest

    try:
        ad = AdRequest.objects.select_related("category", "user").get(pk=request_id)
    except AdRequest.DoesNotExist:
        logger.warning("generate_request_image: AdRequest pk=%s not found", request_id)
        return None

    width = STORY_WIDTH if is_story else FEED_WIDTH
    height = STORY_HEIGHT if is_story else FEED_HEIGHT
    base_dir = Path(settings.BASE_DIR)
    config = _load_banner_config() or {}
    # Apply story Y offset to config coords
    def layer(k: str) -> dict:
        c = dict(config.get(k, config.get("message" if k == "description" else k, {})))
        if is_story and c and isinstance(c.get("y"), (int, float)):
            c["y"] = int(c["y"]) + STORY_Y_OFFSET
        return c

    cat_conf = layer("category")
    desc_conf = layer("description")
    phone_conf = layer("phone")

    img = _load_background(width, height, base_dir)
    if not img:
        return None
    draw = ImageDraw.Draw(img)

    cat_size = _coerce_int(cat_conf.get("size"), 93, 1, 400)
    desc_size = _coerce_int(desc_conf.get("size"), 58, 1, 400)
    phone_size = _coerce_int(phone_conf.get("size"), 48, 20, 400)
    font_cat = _load_banner_font(ImageFont, cat_size, cat_conf.get("font_path"))
    font_desc = _load_banner_font(ImageFont, desc_size, desc_conf.get("font_path"))
    font_phone = _load_english_font(ImageFont, phone_size)

    cat_color = _hex_to_rgb(cat_conf.get("color") or "#EEFF00")
    desc_color = _hex_to_rgb(desc_conf.get("color") or "#FFFFFF")
    phone_color = _hex_to_rgb(phone_conf.get("color") or "#131111")

    cat_x = _coerce_int(cat_conf.get("x"), 180, 0, width)
    cat_y = _coerce_int(cat_conf.get("y"), 288, 0, height)
    desc_x = _coerce_int(desc_conf.get("x"), 215, 0, width)
    desc_y = _coerce_int(desc_conf.get("y"), 598, 0, height)
    phone_x = _coerce_int(phone_conf.get("x"), 300, 0, width)
    phone_y = _coerce_int(phone_conf.get("y"), 1150, 0, height)
    max_width = _coerce_int(desc_conf.get("max_width"), 650, 1, width)

    title = (
        ad.get_category_display_fa()
        if hasattr(ad, "get_category_display_fa")
        else (ad.get_category_display() if ad.category else "آگهی")
    )
    if title:
        draw.text((cat_x, cat_y), title[:200], fill=cat_color, font=font_cat)

    content = (ad.content or "")[:1500]
    for line in _wrap_text(draw, content, font_desc, max_width):
        draw.text((desc_x, desc_y), line[:500], fill=desc_color, font=font_desc)
        bbox = draw.textbbox((0, 0), line, font=font_desc)
        desc_y += (bbox[3] - bbox[1]) + 6
        if desc_y > height - 150:
            break

    phone = ""
    contact = getattr(ad, "contact_snapshot", None) or {}
    phone = (contact.get("phone") or "").strip()
    if not phone and ad.user_id:
        phone = (ad.user.phone_number or "").strip()
    if phone:
        draw.text((phone_x, phone_y), phone[:60], fill=phone_color, font=font_phone)

    media_root = getattr(settings, "MEDIA_ROOT", base_dir / "media") or (base_dir / "media")
    out_dir = Path(media_root) / "instagram"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = f"request_{request_id}_{uuid.uuid4().hex[:8]}.png"
    path = out_dir / name
    img.save(path, format="PNG", optimize=True)
    return str(path.resolve())
