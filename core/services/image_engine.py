"""
High-level image engine for AdTemplate-based ad generation.

Features:
- Uses Pillow for compositing.
- Uses arabic_reshaper + bidi.get_display for proper Persian text rendering.
- Reads coordinates from AdTemplate.coordinates JSON.
"""

import logging
import uuid
from pathlib import Path

from django.conf import settings

from core.models import AdTemplate, default_adtemplate_coordinates

logger = logging.getLogger(__name__)

# Default template background (used when AdTemplate has no background_image)
DEFAULT_TEMPLATE_IMAGE_REL = "static/images/default_template/Template.png"


def _ensure_deps():
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:  # pragma: no cover - runtime guard
        logger.warning("Pillow not installed; run: pip install Pillow")
        return None, None, None, None

    try:
        import arabic_reshaper  # type: ignore
        from bidi.algorithm import get_display  # type: ignore
    except ImportError:  # pragma: no cover - runtime guard
        logger.warning(
            "Persian text deps not installed; run: pip install arabic-reshaper python-bidi"
        )
        return Image, ImageDraw, ImageFont, (None, None)

    return Image, ImageDraw, ImageFont, (arabic_reshaper, get_display)


def _shape_persian(text: str, reshaper, get_display):
    """Return shaped + bidi-corrected text for Persian/Arabic."""
    if not text:
        return ""
    if not reshaper or not get_display:
        # Fallback: return raw text if libs missing
        return text
    try:
        reshaped = reshaper.reshape(text)
        return get_display(reshaped)
    except Exception as e:  # pragma: no cover - safety
        logger.warning("Failed to shape Persian text: %s", e)
        return text


def _load_font(base_font_path: str | None, font_path_override: str | None, ImageFont, size: int):
    """
    Load a TrueType font with optional per-element override.
    Order:
      1. font_path in coordinates (absolute or BASE_DIR-relative)
      2. template.font_file
      3. common fallback fonts (Vazir/Samim/DejaVu)
    """
    paths = []
    base_dir = Path(settings.BASE_DIR)

    if font_path_override:
        p = Path(font_path_override)
        if not p.is_absolute():
            p = base_dir / font_path_override
        paths.append(p)

    if base_font_path:
        paths.append(Path(base_font_path))

    # Default and fallbacks (Persian.ttf is the project default for Pillow)
    for rel in [
        "static/fonts/Persian.ttf",
        "static/fonts/Vazir.ttf",
        "static/fonts/Samim.ttf",
        "static/fonts/DejaVuSans.ttf",
    ]:
        paths.append(base_dir / rel)

    for p in paths:
        try:
            if p.exists():
                return ImageFont.truetype(str(p), size)
        except OSError:
            continue

    return ImageFont.load_default()


def _hex_to_rgb(value: str):
    value = (value or "#FFFFFF").strip()
    if value.startswith("#"):
        value = value[1:]
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    try:
        return (
            int(value[0:2], 16),
            int(value[2:4], 16),
            int(value[4:6], 16),
        )
    except (ValueError, IndexError):
        return (255, 255, 255)


def _wrap_persian_text(draw, text: str, font, max_width: int, reshaper, get_display):
    """Word-wrap text to fit within max_width, aware of Persian shaping for measurement."""
    if max_width <= 0:
        return [text] if text else []

    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        words = paragraph.split()
        current: list[str] = []
        for w in words:
            candidate = " ".join(current + [w])
            shaped = _shape_persian(candidate, reshaper, get_display)
            bbox = draw.textbbox((0, 0), shaped, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current.append(w)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [w]
        if current:
            lines.append(" ".join(current))
    return lines


def _get_media_root() -> Path:
    media_root = getattr(settings, "MEDIA_ROOT", None)
    if not media_root:
        media_root = Path(settings.BASE_DIR) / "media"
    return Path(media_root)


def create_ad_image(
    template_id: int,
    category: str,
    text: str,
    phone: str,
    *,
    background_file=None,
) -> str | None:
    """
    Generate ad image from an AdTemplate and return filesystem path.

    - Loads AdTemplate by id for coordinates and fonts.
    - background_file: optional override for the background image. Can be:
      - str (path to a file),
      - file-like object (e.g. UploadedFile, ContentFile),
      - None to use the template's background_image.
    - Renders category, description, and phone with Persian-aware shaping.
    - Saves PNG into MEDIA_ROOT/generated_ads/ and returns absolute path.
    """
    Image, ImageDraw, ImageFont, deps = _ensure_deps()
    if not Image:
        return None

    reshaper, get_display = deps if isinstance(deps, tuple) else (None, None)

    try:
        tpl = AdTemplate.objects.get(pk=template_id)
    except AdTemplate.DoesNotExist:
        logger.warning("create_ad_image: AdTemplate pk=%s not found", template_id)
        return None

    if not background_file and not tpl.background_image:
        # Use default template image so generation still works
        default_bg = Path(settings.BASE_DIR) / DEFAULT_TEMPLATE_IMAGE_REL
        if default_bg.exists():
            background_file = str(default_bg)
        else:
            logger.warning("create_ad_image: template %s has no background_image and default %s not found", tpl.pk, default_bg)
            return None

    coords = default_adtemplate_coordinates()
    try:
        user_coords = tpl.coordinates or {}
        for key, value in user_coords.items():
            if key in coords and isinstance(value, dict):
                coords[key].update({k: v for k, v in value.items() if v is not None})
    except Exception as e:
        logger.warning("create_ad_image: invalid coordinates for template %s: %s", tpl.pk, e)

    try:
        if background_file is not None:
            if isinstance(background_file, (str, Path)):
                img = Image.open(Path(background_file)).convert("RGB")
            else:
                # file-like (UploadedFile, ContentFile, etc.)
                img = Image.open(background_file).convert("RGB")
        elif tpl.background_image:
            bg_path = Path(tpl.background_image.path)
            if bg_path.exists():
                img = Image.open(bg_path).convert("RGB")
            else:
                default_bg = Path(settings.BASE_DIR) / DEFAULT_TEMPLATE_IMAGE_REL
                img = Image.open(default_bg).convert("RGB")
        else:
            default_bg = Path(settings.BASE_DIR) / DEFAULT_TEMPLATE_IMAGE_REL
            img = Image.open(default_bg).convert("RGB")
    except Exception as e:
        logger.warning("create_ad_image: failed to load background for template %s: %s", tpl.pk, e)
        return None

    draw = ImageDraw.Draw(img)

    base_font_path = tpl.font_file.path if tpl.font_file else None

    # Category
    c_conf = coords.get("category", {})
    cat_font = _load_font(
        base_font_path,
        c_conf.get("font_path") or "",
        ImageFont,
        int(c_conf.get("size") or 48),
    )
    cat_color = _hex_to_rgb(c_conf.get("color") or "#FFFFFF")
    cat_x = int(c_conf.get("x") or 0)
    cat_y = int(c_conf.get("y") or 0)
    cat_text = _shape_persian(category or "", reshaper, get_display)
    if cat_text:
        draw.text((cat_x, cat_y), cat_text, fill=cat_color, font=cat_font)

    # Description (body)
    d_conf = coords.get("description", {})
    desc_font = _load_font(
        base_font_path,
        d_conf.get("font_path") or "",
        ImageFont,
        int(d_conf.get("size") or 32),
    )
    desc_color = _hex_to_rgb(d_conf.get("color") or "#FFFFFF")
    desc_x = int(d_conf.get("x") or 0)
    desc_y = int(d_conf.get("y") or 0)
    max_width = int(d_conf.get("max_width") or 800)

    for line in _wrap_persian_text(draw, text or "", desc_font, max_width, reshaper, get_display):
        shaped_line = _shape_persian(line, reshaper, get_display)
        draw.text((desc_x, desc_y), shaped_line, fill=desc_color, font=desc_font)
        bbox = draw.textbbox((0, 0), shaped_line, font=desc_font)
        desc_y += (bbox[3] - bbox[1]) + 6

    # Phone
    p_conf = coords.get("phone", {})
    phone_font = _load_font(
        base_font_path,
        p_conf.get("font_path") or "",
        ImageFont,
        int(p_conf.get("size") or 28),
    )
    phone_color = _hex_to_rgb(p_conf.get("color") or "#FFFF00")
    phone_x = int(p_conf.get("x") or 0)
    phone_y = int(p_conf.get("y") or 0)
    phone_text = _shape_persian(phone or "", reshaper, get_display)
    if phone_text:
        draw.text((phone_x, phone_y), phone_text, fill=phone_color, font=phone_font)

    # Save
    media_root = _get_media_root()
    out_dir = media_root / "generated_ads"
    out_dir.mkdir(parents=True, exist_ok=True)

    filename = f"ad_{tpl.pk}_{uuid.uuid4().hex[:8]}.png"
    out_path = out_dir / filename
    try:
        img.save(out_path, format="PNG", optimize=True)
    except Exception as e:
        logger.warning("create_ad_image: failed to save image for template %s: %s", tpl.pk, e)
        return None

    return str(out_path.resolve())


def make_story_image(feed_image_path: str) -> str | None:
    """
    Create a 9:16 (1080x1920) story image from a feed image.
    Pastes the feed image centered and scaled to fit; background is black.
    Returns filesystem path to the saved story image, or None on failure.
    """
    Image, _, _, _ = _ensure_deps()
    if not Image:
        return None
    path = Path(feed_image_path)
    if not path.exists():
        logger.warning("make_story_image: path does not exist: %s", feed_image_path)
        return None
    try:
        feed = Image.open(path).convert("RGB")
    except Exception as e:
        logger.warning("make_story_image: failed to open feed image: %s", e)
        return None
    w, h = feed.size
    target_w, target_h = 1080, 1920
    scale = min(target_w / w, target_h / h)
    new_w = int(w * scale)
    new_h = int(h * scale)
    resized = feed.resize((new_w, new_h), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (target_w, target_h), (0, 0, 0))
    x = (target_w - new_w) // 2
    y = (target_h - new_h) // 2
    canvas.paste(resized, (x, y))
    media_root = _get_media_root()
    out_dir = media_root / "generated_ads"
    out_dir.mkdir(parents=True, exist_ok=True)
    filename = f"story_{uuid.uuid4().hex[:8]}.png"
    out_path = out_dir / filename
    try:
        canvas.save(out_path, format="PNG", optimize=True)
    except Exception as e:
        logger.warning("make_story_image: failed to save: %s", e)
        return None
    return str(out_path.resolve())

