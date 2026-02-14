"""
High-level image engine for AdTemplate-based ad generation.

Features:
- Uses Pillow for compositing.
- Coordinates, font sizes, and colors from static/banner_config.json only (no hardcoded values).
- Font: static/fonts/YekanBakh-Bold.ttf for Category and Description; no pseudo-bold stroke.
- Raw text passed to draw.text() (no arabic_reshaper or python-bidi).
- POST: 1080x1080 (Square); STORY: 1080x1920 (9:16); Story uses Y+285 offset.
- Three text layers: Category, Description, Phone.
"""

import logging
import os
import uuid
from pathlib import Path

from django.conf import settings

from core.services.log_service import log_exception

from core.models import (
    AdTemplate,
    default_adtemplate_coordinates,
    FORMAT_POST,
    FORMAT_STORY,
    FORMAT_DIMENSIONS,
    STORY_SAFE_TOP,
    STORY_SAFE_BOTTOM,
    STORY_Y_OFFSET,
)

logger = logging.getLogger(__name__)

# Default template background (used when AdTemplate has no background_image)
DEFAULT_TEMPLATE_IMAGE_REL = "static/images/default_template/Template.png"

# ── Banner font: static/fonts/YekanBakh-Bold.ttf only (no fake bold) ──
def _get_persian_font_path():
    base = Path(settings.BASE_DIR)
    candidate = base / "static" / "fonts" / "YekanBakh-Bold.ttf"
    if candidate.exists():
        return str(candidate)
    if (base / "YekanBakh-Bold.ttf").exists():
        return str(base / "YekanBakh-Bold.ttf")
    return None


PERSIAN_FONT_PATH = _get_persian_font_path()
assert PERSIAN_FONT_PATH, (
    "FATAL: Banner font not found. Place YekanBakh-Bold.ttf in static/fonts/ or project root."
)


def _ensure_deps():
    """Import Pillow or fail fast. Returns (Image, ImageDraw, ImageFont, ImageFilter)."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except ImportError:  # pragma: no cover - runtime guard
        logger.critical("Pillow not installed; run: pip install Pillow")
        return None, None, None, None
    return Image, ImageDraw, ImageFont, ImageFilter


# ── Text preparation: no arabic_reshaper or python-bidi; raw text to draw.text() ──

def _normalize_to_western_digits(text: str) -> str:
    """
    Convert Persian/Arabic numerals (۰-۹, ٠-٩) to Western Arabic (0-9).
    This ensures phone numbers always render with Latin digits regardless of
    which font is used.
    """
    if not text:
        return text
    _PERSIAN = "۰۱۲۳۴۵۶۷۸۹"
    _ARABIC = "٠١٢٣٤٥٦٧٨٩"
    _WESTERN = "0123456789"
    table = str.maketrans(_PERSIAN + _ARABIC, _WESTERN * 2)
    return text.translate(table)


def prepare_text(text: str, *, is_phone: bool = False) -> str:
    """
    Prepare text for image drawing (Category/Description/Phone layers).

    No arabic_reshaper or python-bidi: raw text is passed to draw.text().
    For phone numbers (is_phone=True): convert Persian/Arabic digits to Western (0-9).
    """
    if not text:
        return ""
    if is_phone:
        return _normalize_to_western_digits(text).strip()
    return text.strip()


def _resolve_absolute(p: Path) -> Path:
    """Ensure a Path is absolute by resolving against BASE_DIR if needed."""
    if p.is_absolute():
        return p
    return Path(settings.BASE_DIR) / p


def _load_font(ImageFont, size: int, font_path_override: str | None = None):
    """
    Load banner font for Category and Description (Farsi text).
    Default: static/fonts/YekanBakh-Bold.ttf (PERSIAN_FONT_PATH).
    If font_path_override is set (e.g. from banner_config.json), resolve against BASE_DIR and use when valid.
    Phone numbers use _load_english_font. No stroke/pseudo-bold — use .ttf weight only.
    """
    path = PERSIAN_FONT_PATH
    override = (font_path_override or "").strip()
    if override:
        resolved = _resolve_absolute(Path(override))
        if resolved.exists():
            path = str(resolved)
    font = ImageFont.truetype(path, size)
    logger.debug("Loaded banner font: %s (size %d)", path, size)
    return font


# Preferred font for phone numbers on ad banners
PHONE_FONT_NAME = "monstrat.ttf"


def _load_english_font(font_path_override: str | None, ImageFont, size: int):
    """
    Load a Latin/English TrueType font for the Phone layer.
    Prefers monstrat.ttf for ad banners; then explicit override; then fallbacks.

    Search order:
    1. Explicit override path (from coordinates JSON).
    2. monstrat.ttf (project font for phone numbers).
    3. Bold system fonts: arialbd.ttf, trebucbd.ttf (Windows).
    4. Project fonts: English.ttf, Roboto.ttf, etc.
    5. Regular system fonts: Arial, Segoe UI, DejaVuSans.
    6. Pillow default.
    """
    paths: list[Path] = []
    base_dir = Path(settings.BASE_DIR)
    media_root = _resolve_absolute(
        Path(getattr(settings, "MEDIA_ROOT", base_dir / "media"))
    )

    if font_path_override:
        p = _resolve_absolute(Path(font_path_override))
        paths.append(p)

    # monstrat.ttf: preferred font for phone numbers on ad banners
    for d in [base_dir / "static" / "fonts", media_root / "ad_templates" / "fonts"]:
        paths.append(d / PHONE_FONT_NAME)

    import platform
    # Prefer bold system fonts for phone numbers (clear, heavy digits)
    if platform.system() == "Windows":
        win_fonts = Path("C:/Windows/Fonts")
        for name in ["arialbd.ttf", "trebucbd.ttf", "segoeuib.ttf", "calibrib.ttf"]:
            paths.append(win_fonts / name)

    # Project English fonts
    english_font_names = ["English.ttf", "Roboto.ttf", "Inter.ttf", "OpenSans.ttf"]
    search_dirs = [
        media_root / "ad_templates" / "fonts",
        base_dir / "static" / "fonts",
    ]
    for d in search_dirs:
        for name in english_font_names:
            paths.append(d / name)

    # Regular system fonts
    if platform.system() == "Windows":
        win_fonts = Path("C:/Windows/Fonts")
        for name in ["arial.ttf", "segoeui.ttf", "calibri.ttf", "verdana.ttf", "trebuc.ttf"]:
            paths.append(win_fonts / name)
    else:
        linux_dirs = [
            Path("/usr/share/fonts/truetype/dejavu"),
            Path("/usr/share/fonts/truetype/liberation"),
            Path("/usr/share/fonts/TTF"),
            Path("/System/Library/Fonts"),
        ]
        for d in linux_dirs:
            for name in ["DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "LiberationSans-Bold.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"]:
                paths.append(d / name)

    paths.append(base_dir / "static" / "fonts" / "DejaVuSans.ttf")

    for p in paths:
        try:
            if p.exists():
                font = ImageFont.truetype(str(p), size)
                logger.debug("Loaded English font for phone: %s (size %d)", p.name, size)
                return font
        except OSError as e:
            logger.debug("Failed to load English font %s: %s", p, e)
            continue

    logger.warning(
        "No English font found; phone numbers will use Pillow default. "
        "Place monstrat.ttf in static/fonts/ or media/ad_templates/fonts/ for best results."
    )
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


def _coerce_int(value, *, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    """Safely coerce a value to int with optional bounds; fallback to default on failure."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = default
    if minimum is not None:
        number = max(minimum, number)
    if maximum is not None:
        number = min(maximum, number)
    return number


def draw_spaced_text(
    draw_obj,
    text: str,
    font,
    color,
    *,
    x: int,
    y: int,
    align: str = "center",
    area_width: int | None = None,
    spacing_px: int = 4,
):
    """
    Draw text character-by-character with custom inter-character spacing (kerning).

    Pillow's draw.text() has no letter-spacing option. This function manually
    draws each character and shifts the cursor by (char_advance + spacing_px),
    producing a clear look for phone numbers. No stroke/pseudo-bold — font weight only.
    """
    if not text:
        return

    stroke_w = 0

    # Step 1: Calculate total rendered width with spacing
    char_advances: list[float] = []
    for ch in text:
        bbox = draw_obj.textbbox((0, 0), ch, font=font, stroke_width=stroke_w)
        char_advances.append(bbox[2] - bbox[0])

    num_gaps = max(0, len(text) - 1)
    total_width = sum(char_advances) + (num_gaps * spacing_px)

    # Step 2: Determine starting X based on alignment
    aw = area_width if (area_width is not None and area_width > 0) else int(total_width)
    if align == "center":
        start_x = x + (aw - total_width) / 2
    elif align == "right":
        start_x = x
    else:  # "left" — push to right edge (RTL-aware: "left" in our engine means right-aligned)
        start_x = x + max(0, aw - total_width)

    # Step 3: Draw each character individually
    current_x = start_x
    for i, ch in enumerate(text):
        draw_obj.text(
            (int(current_x), y),
            ch,
            fill=color,
            font=font,
            stroke_width=stroke_w,
            stroke_fill=color,
        )
        current_x += char_advances[i]
        if i < num_gaps:
            current_x += spacing_px


def _wrap_persian_text(draw, text: str, font, max_width: int):
    """
    Word-wrap text to fit within max_width. Raw text (no reshaping).
    """
    if max_width <= 0:
        return [text] if text else []

    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue

        words = paragraph.split()
        current: list[str] = []

        for w in words:
            candidate_words = current + [w]
            candidate_text = " ".join(candidate_words)
            bbox = draw.textbbox((0, 0), candidate_text, font=font)
            width = bbox[2] - bbox[0]

            if width <= max_width:
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


# ---------------------------------------------------------------------------
# Banner config: single source of truth for coordinates, font sizes, colors
# ---------------------------------------------------------------------------

# Fallback only when static/banner_config.json is missing (mirrors that file).
_DEFAULT_BANNER_CONFIG = {
    "category": {"x": 180, "y": 288, "size": 93, "color": "#EEFF00", "font_path": "static/fonts/YekanBakh-Bold.ttf", "max_width": 700, "align": "center"},
    "message": {"x": 215, "y": 598, "size": 58, "color": "#FFFFFF", "font_path": "static/fonts/YekanBakh-Bold.ttf", "max_width": 650, "align": "center"},
    "phone": {"x": 300, "y": 1150, "size": 48, "color": "#131111", "max_width": 450, "align": "center", "letter_spacing": 2},
}


def _load_banner_config() -> dict | None:
    """
    Load static/banner_config.json for coordinates, font sizes, and colors.
    Used as the single source for all ad banner generation (Feed and Story).
    Returns None if file is missing or invalid.
    """
    import json
    config_path = Path(settings.BASE_DIR) / "static" / "banner_config.json"
    if not config_path.exists():
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return None
        # Map "message" -> "description" for compatibility
        if "message" in data and "description" not in data:
            data = dict(data)
            data["description"] = data.pop("message")
        return data
    except Exception as e:
        logger.debug("_load_banner_config: could not load %s: %s", config_path, e)
        return None


# ---------------------------------------------------------------------------
# Story coordinate adaptation: simple Y+285 offset
# ---------------------------------------------------------------------------

def get_story_coordinates(post_coords: dict, post_width: int = 1080, post_height: int = 1350) -> dict:
    """
    Convert post-format coordinates to story-format coordinates.

    Simple approach: shift all Y values by +STORY_Y_OFFSET (285px).
    This centers the post content within the 9:16 story frame.

    Args:
        post_coords: The post-format coordinates dict (category, description, phone).
        post_width: Width of the post canvas (unused, kept for API compat).
        post_height: Height of the post canvas (unused, kept for API compat).

    Returns:
        New coordinates dict with Y values shifted for 1080x1920 story canvas.
    """
    story_coords = {}
    for layer_key, layer_conf in post_coords.items():
        if not isinstance(layer_conf, dict):
            story_coords[layer_key] = layer_conf
            continue
        new_conf = dict(layer_conf)
        new_conf['y'] = layer_conf.get('y', 0) + STORY_Y_OFFSET
        story_coords[layer_key] = new_conf
    return story_coords


def clamp_to_safety_zone(coords: dict, canvas_height: int = 1920) -> dict:
    """
    Ensure all Y coordinates in the given coords dict respect
    the Story safety zones (top 250px, bottom 250px).
    """
    safe_top = STORY_SAFE_TOP
    safe_bottom = canvas_height - STORY_SAFE_BOTTOM

    clamped = {}
    for layer_key, layer_conf in coords.items():
        if not isinstance(layer_conf, dict):
            clamped[layer_key] = layer_conf
            continue
        new_conf = dict(layer_conf)
        y = new_conf.get('y', 0)
        new_conf['y'] = max(safe_top, min(y, safe_bottom - new_conf.get('size', 32)))
        clamped[layer_key] = new_conf

    return clamped


# ---------------------------------------------------------------------------
# Background handling for Story format
# ---------------------------------------------------------------------------

def _build_story_canvas(img, Image, ImageFilter):
    """
    Build a 1080x1920 story canvas from the source image.

    - If the image is already ~9:16, just resize to fit.
    - If the image is square or non-9:16, create a blurred + darkened version
      as background, then center the original image on top.
    """
    target_w, target_h = FORMAT_DIMENSIONS[FORMAT_STORY]
    src_w, src_h = img.size
    src_ratio = src_w / max(1, src_h)
    target_ratio = target_w / target_h  # 0.5625

    # If already close to 9:16 (within 10% tolerance), just resize
    if abs(src_ratio - target_ratio) < 0.06:
        return img.resize((target_w, target_h), Image.Resampling.LANCZOS)

    # Build blurred background: scale source to fill the entire 9:16 frame
    bg_scale = max(target_w / src_w, target_h / src_h)
    bg_w = int(src_w * bg_scale)
    bg_h = int(src_h * bg_scale)
    bg = img.resize((bg_w, bg_h), Image.Resampling.LANCZOS)

    # Center-crop to target
    left = (bg_w - target_w) // 2
    top = (bg_h - target_h) // 2
    bg = bg.crop((left, top, left + target_w, top + target_h))

    # Apply heavy blur + darken
    bg = bg.filter(ImageFilter.GaussianBlur(radius=30))
    from PIL import ImageEnhance
    enhancer = ImageEnhance.Brightness(bg)
    bg = enhancer.enhance(0.35)  # 35% brightness (dark overlay)

    # Scale the original image to fit within the target, preserving aspect ratio
    fit_scale = min(target_w / src_w, target_h / src_h)
    max_fit_h = target_h - STORY_SAFE_TOP - STORY_SAFE_BOTTOM + 100  # slight overflow OK
    if src_h * fit_scale > max_fit_h:
        fit_scale = max_fit_h / src_h

    new_w = int(src_w * fit_scale)
    new_h = int(src_h * fit_scale)
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)

    # Center on canvas
    paste_x = (target_w - new_w) // 2
    paste_y = (target_h - new_h) // 2
    bg.paste(resized, (paste_x, paste_y))

    return bg


# ---------------------------------------------------------------------------
# Main image generation
# ---------------------------------------------------------------------------

def create_ad_image(
    template_id: int,
    category: str,
    text: str,
    phone: str,
    *,
    background_file=None,
    format_type: str = FORMAT_POST,
    use_default_phone_coords: bool = False,
    output_filename: str | None = None,
    coords_override: dict | None = None,
    output_path: str | Path | None = None,
) -> str | None:
    """
    Generate ad image from an AdTemplate and return filesystem path.

    Coordinates, font sizes, and colors: static/banner_config.json (primary),
    then template.coordinates, then coords_override. Font: YekanBakh-Bold.ttf by default; no pseudo-bold.

    Layers: Category, Description (body text), Phone.

    Args:
        template_id: Primary key of the AdTemplate.
        category: Category text (e.g. 'فروش ویژه').
        text: Description body text.
        phone: Phone number text.
        background_file: Optional override for background. Can be str path,
                         file-like object, or None to use template's image.
        format_type: 'POST' (1080x1080) or 'STORY' (1080x1920).
        use_default_phone_coords: If True, ignore template's phone coords; use code defaults.
        output_filename: Optional filename for output (e.g. 'example_ad_test.png').
        coords_override: Optional dict to override coordinates (keys: category, message/description, phone).
        output_path: Optional absolute path for output (overrides default subdir + filename).

    Returns:
        Absolute filesystem path to the saved PNG, or None on failure.
    """
    Image, ImageDraw, ImageFont, ImageFilter = _ensure_deps()
    if not Image:
        return None

    try:
        tpl = AdTemplate.objects.get(pk=template_id)
    except AdTemplate.DoesNotExist:
        logger.warning("create_ad_image: AdTemplate pk=%s not found", template_id)
        return None

    if not background_file and not tpl.background_image:
        default_bg = Path(settings.BASE_DIR) / DEFAULT_TEMPLATE_IMAGE_REL
        if default_bg.exists():
            background_file = str(default_bg)
        else:
            logger.warning("create_ad_image: template %s has no background_image and default %s not found", tpl.pk, default_bg)
            return None

    # Load coordinates: banner_config.json only -> template.coordinates -> coords_override
    coords = default_adtemplate_coordinates()
    banner_config = _load_banner_config()
    if banner_config:
        for key, value in banner_config.items():
            if key in coords and isinstance(value, dict):
                coords[key].update({k: v for k, v in value.items() if v is not None})
    else:
        # Fallback when JSON missing: use built-in mirror of banner_config.json
        for key, value in _DEFAULT_BANNER_CONFIG.items():
            k = "description" if key == "message" else key
            if k in coords and isinstance(value, dict):
                coords[k].update({k2: v for k2, v in value.items() if v is not None})
    try:
        user_coords = dict(tpl.coordinates or {})
        if "message" in user_coords and "description" not in user_coords:
            user_coords["description"] = user_coords.pop("message")
        for key, value in user_coords.items():
            if key in coords and isinstance(value, dict):
                if key == "phone" and use_default_phone_coords:
                    continue
                coords[key].update({k: v for k, v in value.items() if v is not None})
    except Exception as e:
        logger.warning("create_ad_image: invalid template coordinates for template %s: %s", tpl.pk, e)
    if coords_override:
        try:
            override = dict(coords_override)
            if "message" in override and "description" not in override:
                override["description"] = override.pop("message")
            for key, value in override.items():
                if key in coords and isinstance(value, dict):
                    if key == "phone" and use_default_phone_coords:
                        continue
                    coords[key].update({k: v for k, v in value.items() if v is not None})
        except Exception as e:
            logger.warning("create_ad_image: invalid coords_override: %s", e)

    # For Story: apply Y+285 offset to all coordinates (no separate JSON needed)
    is_story = format_type == FORMAT_STORY
    if is_story:
        coords = get_story_coordinates(coords)

    # Load source image
    try:
        if background_file is not None:
            if isinstance(background_file, (str, Path)):
                img = Image.open(Path(background_file)).convert("RGB")
            else:
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

    # POST: 1080x1080 (Square); resize background to exact dimensions
    if not is_story:
        target_w, target_h = FORMAT_DIMENSIONS[FORMAT_POST]
        if img.size != (target_w, target_h):
            img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    # Story: build the 9:16 canvas with blurred background
    else:
        img = _build_story_canvas(img, Image, ImageFilter)

    draw = ImageDraw.Draw(img)

    def _draw_aligned_line(draw_obj, txt: str, x: int, y: int, font_obj, color, align: str, max_w: int | None = None, bold: bool = False):
        if not txt:
            return
        # No pseudo-bold: use .ttf weight only (stroke_w=0)
        stroke_w = 0
        bbox = draw_obj.textbbox((0, 0), txt, font=font_obj, stroke_width=stroke_w)
        text_w = max(1, bbox[2] - bbox[0])
        area_w = max_w if (max_w is not None and max_w > 0) else text_w
        anchor_x = x
        if align == "center":
            anchor_x = x + int((area_w - text_w) / 2)
        elif align == "left":
            anchor_x = x + int(max(0, area_w - text_w))
        draw_obj.text(
            (anchor_x, y), txt, fill=color, font=font_obj,
            stroke_width=stroke_w, stroke_fill=color,
        )

    # ── Category Layer (YekanBakh-Bold.ttf from config, no pseudo-bold) ──
    c_conf = coords.get("category", {})
    cat_font = _load_font(
        ImageFont,
        _coerce_int(c_conf.get("size"), default=93, minimum=1, maximum=400),
        c_conf.get("font_path"),
    )
    cat_color = _hex_to_rgb(c_conf.get("color") or "#EEFF00")
    cat_x = _coerce_int(c_conf.get("x"), default=0, minimum=-img.width * 2, maximum=img.width * 2)
    cat_y = _coerce_int(c_conf.get("y"), default=0, minimum=-img.height * 2, maximum=img.height * 2)
    cat_align = (c_conf.get("align") or "center").strip().lower()
    if cat_align not in ("left", "center", "right"):
        cat_align = "center"
    cat_max_w = _coerce_int(c_conf.get("max_width"), default=700, minimum=1, maximum=img.width * 2)
    # No pseudo-bold (stroke): YekanBakh-Bold.ttf handles weight naturally
    cat_text = prepare_text(category or "", is_phone=False)
    if cat_text:
        _draw_aligned_line(draw, cat_text, cat_x, cat_y, cat_font, cat_color, cat_align, cat_max_w, bold=False)

    # ── Description Layer (YekanBakh-Bold.ttf from config, no pseudo-bold, multi-line) ──
    d_conf = coords.get("description", {})
    desc_font = _load_font(
        ImageFont,
        _coerce_int(d_conf.get("size"), default=58, minimum=1, maximum=400),
        d_conf.get("font_path"),
    )
    desc_color = _hex_to_rgb(d_conf.get("color") or "#FFFFFF")
    desc_x = _coerce_int(d_conf.get("x"), default=0, minimum=-img.width * 2, maximum=img.width * 2)
    desc_y = _coerce_int(d_conf.get("y"), default=0, minimum=-img.height * 2, maximum=img.height * 2)
    max_width = _coerce_int(d_conf.get("max_width"), default=650, minimum=1, maximum=img.width * 2)
    desc_align = (d_conf.get("align") or "center").strip().lower()
    if desc_align not in ("left", "center", "right"):
        desc_align = "center"

    wrapped_lines = _wrap_persian_text(draw, text or "", desc_font, max_width)
    for line in wrapped_lines:
        _draw_aligned_line(draw, line, desc_x, desc_y, desc_font, desc_color, desc_align, max_width, bold=False)
        bbox = draw.textbbox((0, 0), line, font=desc_font, stroke_width=0)
        desc_y += (bbox[3] - bbox[1]) + 6

    # ── Phone Layer: English font only (monstrat/arialbd/trebucbd), LTR, Western digits ──
    # Default: x300 y1150, size 48, max_width 450, letter_spacing 2.
    # is_phone=True: no reshaping, no RTL — numbers stay strictly LTR.
    PHONE_BOTTOM_PADDING = 80  # Minimum clearance from image bottom to avoid overlap
    p_conf = coords.get("phone", {})
    phone_font = _load_english_font(
        p_conf.get("font_path") or "",
        ImageFont,
        _coerce_int(p_conf.get("size"), default=48, minimum=20, maximum=400),
    )
    phone_color = _hex_to_rgb(p_conf.get("color") or "#131111")
    phone_x = _coerce_int(p_conf.get("x"), default=300, minimum=-img.width * 2, maximum=img.width * 2)
    phone_y_raw = _coerce_int(p_conf.get("y"), default=1150, minimum=-img.height * 2, maximum=img.height * 2)
    # Clamp Y so phone bottom stays above img bottom (min 80px clearance)
    est_phone_height = int(phone_font.size * 1.2)
    max_phone_y = img.height - PHONE_BOTTOM_PADDING - est_phone_height
    phone_y = min(phone_y_raw, max_phone_y)
    phone_align = (p_conf.get("align") or "center").strip().lower()
    if phone_align not in ("left", "center", "right"):
        phone_align = "center"
    phone_max_w = _coerce_int(p_conf.get("max_width"), default=450, minimum=1, maximum=img.width * 2)
    # Kerning: reduced spacing so number fits inside frame
    phone_spacing = _coerce_int(p_conf.get("letter_spacing"), default=2, minimum=0, maximum=20)
    # Strict: is_phone=True — no reshaping, no RTL; numbers stay LTR
    phone_text = prepare_text(phone or "", is_phone=True)
    if phone_text:
        draw_spaced_text(
            draw,
            phone_text,
            phone_font,
            phone_color,
            x=phone_x,
            y=phone_y,
            align=phone_align,
            area_width=phone_max_w,
            spacing_px=phone_spacing,
        )

    # Draw safety zone guidelines for Story (debug aid — only when DEBUG)
    if is_story and getattr(settings, 'DEBUG', False):
        for zone_y in (STORY_SAFE_TOP, img.height - STORY_SAFE_BOTTOM):
            for x in range(0, img.width, 20):
                draw.line([(x, zone_y), (min(x + 10, img.width), zone_y)], fill=(255, 50, 50, 80), width=1)

    # Ensure exact dimensions before save: POST 1080x1080, STORY 1080x1920
    target_w, target_h = FORMAT_DIMENSIONS[format_type]
    if img.size != (target_w, target_h):
        img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
        draw = ImageDraw.Draw(img)

    # Save: output_path overrides; else Feed → generated_ads/, Story → generated_stories/
    if output_path is not None:
        out_path = Path(output_path).resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        media_root = _get_media_root()
        subdir = "generated_stories" if is_story else "generated_ads"
        out_dir = media_root / subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        if output_filename:
            filename = output_filename
            if not filename.lower().endswith((".png", ".jpg", ".jpeg")):
                filename = f"{filename}.png"
        else:
            prefix = "story" if is_story else "ad"
            filename = f"{prefix}_{tpl.pk}_{uuid.uuid4().hex[:8]}.png"
        out_path = out_dir / filename
    fmt = "JPEG" if str(out_path).lower().endswith((".jpg", ".jpeg")) else "PNG"
    try:
        save_kw = dict(format=fmt, optimize=True)
        if fmt == "JPEG":
            save_kw["quality"] = 95
        img.save(out_path, **save_kw)
    except Exception as e:
        logger.warning("create_ad_image: failed to save image for template %s: %s", tpl.pk, e)
        return None

    return str(out_path.resolve())


# ---------------------------------------------------------------------------
# High-level API: generate image from an Ad object
# ---------------------------------------------------------------------------

def generate_ad_image(ad, is_story: bool = False) -> str | None:
    """
    Generate an ad image directly from an AdRequest object.

    Uses ad.category.name_fa (Persian) with fallback to ad.category.name.
    Uses ad.content as description (truncated to 250 chars).
    Uses phone from contact_snapshot or user profile.

    Args:
        ad: An AdRequest instance (must be approved).
        is_story: If True, generates 1080x1920 story format.

    Returns:
        Filesystem path to the generated image, or None on failure.
    """
    template = AdTemplate.objects.filter(is_active=True).first()
    if not template:
        logger.warning("generate_ad_image: no active AdTemplate")
        return None

    # Category: prefer Persian name
    category_text = ""
    if ad.category:
        category_text = getattr(ad.category, 'name_fa', '') or ad.category.name
    if not category_text:
        category_text = ad.get_category_display_fa() if hasattr(ad, 'get_category_display_fa') else "سایر"

    # Description: limit to 250 chars
    description = (ad.content or "").strip()[:250]

    # Phone: from contact_snapshot or user profile
    contact = getattr(ad, 'contact_snapshot', None) or {}
    phone = (contact.get('phone') or '').strip() if isinstance(contact, dict) else ''
    if not phone and getattr(ad, 'user_id', None) and ad.user:
        phone = (ad.user.phone_number or '').strip()

    format_type = FORMAT_STORY if is_story else FORMAT_POST
    try:
        return create_ad_image(template.pk, category_text, description, phone, format_type=format_type)
    except Exception as exc:
        log_exception(
            exc,
            'IMAGE_GENERATION',
            f'Image generation failed (Pillow) ad={getattr(ad, "pk", "?")}: {str(exc)[:150]}',
            ad_request=ad if hasattr(ad, 'pk') else None,
            request_data={'template_id': template.pk, 'format_type': format_type, 'category': category_text[:50]},
        )
        raise


def ensure_feed_image(ad) -> bool:
    """
    Ensure ad has generated_image (Feed/Post). Generate and save to model if missing.
    Returns True if ad.generated_image is set (existing or newly generated).
    """
    if getattr(ad, 'generated_image', None) and ad.generated_image:
        try:
            if ad.generated_image.path and Path(ad.generated_image.path).exists():
                return True
        except (ValueError, OSError):
            pass
    path = generate_ad_image(ad, is_story=False)
    if not path:
        return False
    try:
        media_root = _get_media_root()
        rel = str(Path(path).resolve().relative_to(media_root.resolve())).replace('\\', '/')
        ad.generated_image.name = rel
        ad.save(update_fields=['generated_image'])
        logger.info("ensure_feed_image: saved for ad %s -> %s", ad.uuid, rel)
        return True
    except Exception as e:
        logger.warning("ensure_feed_image: failed to attach path for ad %s: %s", ad.uuid, e)
        log_exception(e, 'IMAGE_GENERATION', f'ensure_feed_image attach failed ad={ad.uuid}: {str(e)[:150]}', ad_request=ad)
        return False


def ensure_story_image(ad) -> bool:
    """
    Ensure ad has generated_story_image (9:16 Story). Generate and save to model if missing.
    Returns True if ad.generated_story_image is set (existing or newly generated).
    """
    if getattr(ad, 'generated_story_image', None) and ad.generated_story_image:
        try:
            if ad.generated_story_image.path and Path(ad.generated_story_image.path).exists():
                return True
        except (ValueError, OSError):
            pass
    path = generate_ad_image(ad, is_story=True)
    if not path:
        return False
    try:
        media_root = _get_media_root()
        rel = str(Path(path).resolve().relative_to(media_root.resolve())).replace('\\', '/')
        ad.generated_story_image.name = rel
        ad.save(update_fields=['generated_story_image'])
        logger.info("ensure_story_image: saved for ad %s -> %s", ad.uuid, rel)
        return True
    except Exception as e:
        logger.warning("ensure_story_image: failed to attach path for ad %s: %s", ad.uuid, e)
        log_exception(e, 'IMAGE_GENERATION', f'ensure_story_image attach failed ad={ad.uuid}: {str(e)[:150]}', ad_request=ad)
        return False


def generate_example_ad_banner(
    format_type: str = FORMAT_POST,
    output_filename: str | None = None,
) -> str | None:
    """
    Generate a sample ad banner for testing (e.g. phone font with monstrat.ttf).

    Uses sample Persian category, description, and phone number to verify
    layout and font rendering. Saves to MEDIA_ROOT/generated_ads/ with
    a predictable filename for easy inspection.

    Args:
        format_type: 'POST' (1080x1080) or 'STORY' (1080x1920).
        output_filename: Optional custom filename (e.g. 'example_test.png').
                        Default: example_ad_<format>_<uuid8>.png

    Returns:
        Absolute path to the generated image, or None on failure.

    Example:
        >>> from core.services.image_engine import generate_example_ad_banner
        >>> path = generate_example_ad_banner(format_type='POST')
        >>> print(path)  # .../media/generated_ads/example_ad_POST_abc12345.png
    """
    template = AdTemplate.objects.filter(is_active=True).first()
    if not template:
        logger.warning("generate_example_ad_banner: no active AdTemplate")
        return None

    category_text = "فروش ویژه"
    description_text = "متن نمونه برای تست بنر آگهی و فونت شماره تلفن"
    phone_number = "+44 20 7123 4567"

    fn = (output_filename or "example_ad_test.png")
    path = create_ad_image(
        template.pk,
        category_text,
        description_text,
        phone_number,
        format_type=format_type,
        use_default_phone_coords=True,
        output_filename=fn,
    )
    return path


def delete_old_assets(days: int = 7) -> dict:
    """
    Delete generated ad/story images older than *days*.

    Targets:
      - MEDIA_ROOT/generated_ads/   (ad_*.png, story_*.png)

    Safety rules:
      - Only deletes regular **files** — never directories.
      - Skips essential assets: .gitkeep, fonts (*.ttf, *.otf, *.woff*),
        template backgrounds (anything under ad_templates/), and any
        file whose name starts with "Template".
      - Never recurses into subdirectories.

    Returns:
        {"deleted_count": int, "freed_space_mb": float, "errors": int}
    """
    import time

    if days < 1:
        days = 1  # safety floor

    media_root = _get_media_root()
    target_dirs = [
        media_root / "generated_ads",
        media_root / "generated_stories",
    ]

    # File extensions and prefixes that must NEVER be deleted
    PROTECTED_EXTENSIONS = {".ttf", ".otf", ".woff", ".woff2", ".json"}
    PROTECTED_NAMES = {".gitkeep", ".gitignore", "README.md"}
    PROTECTED_PREFIXES = ("Template",)

    cutoff_ts = time.time() - (days * 86400)

    deleted_count = 0
    freed_bytes = 0
    error_count = 0

    for target_dir in target_dirs:
        if not target_dir.exists() or not target_dir.is_dir():
            continue

        for file_path in target_dir.iterdir():
            # Skip directories — never delete folder structures
            if not file_path.is_file():
                continue

            fname = file_path.name

            # Skip protected files
            if fname in PROTECTED_NAMES:
                continue
            if file_path.suffix.lower() in PROTECTED_EXTENSIONS:
                continue
            if any(fname.startswith(pfx) for pfx in PROTECTED_PREFIXES):
                continue

            try:
                stat = file_path.stat()
                if stat.st_mtime < cutoff_ts:
                    size = stat.st_size
                    file_path.unlink(missing_ok=True)
                    deleted_count += 1
                    freed_bytes += size
            except Exception as exc:
                logger.warning("delete_old_assets: failed to remove %s: %s", file_path, exc)
                error_count += 1

    freed_mb = round(freed_bytes / (1024 * 1024), 2)
    logger.info(
        "delete_old_assets: deleted=%d, freed=%.2f MB, errors=%d (cutoff=%d days)",
        deleted_count, freed_mb, error_count, days,
    )
    return {"deleted_count": deleted_count, "freed_space_mb": freed_mb, "errors": error_count}


def make_story_image(feed_image_path: str) -> str | None:
    """
    Create a 9:16 (1080x1920) story image from a feed image.

    If the feed image is square (1:1), uses blurred+darkened background fill
    instead of stretching. Otherwise scales to fit with black background.

    Returns filesystem path to the saved story image, or None on failure.
    """
    Image, _, _, ImageFilter = _ensure_deps()
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

    canvas = _build_story_canvas(feed, Image, ImageFilter)

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
