"""
High-level image engine for AdTemplate-based ad generation.

Features:
- Uses Pillow for compositing.
- Uses arabic_reshaper + bidi.get_display for proper Persian text rendering.
- Reads coordinates from AdTemplate.coordinates JSON.
- Supports dual format: POST (1080x1350 / 4:5) and STORY (1080x1920 / 9:16).
- Story format: blurred background fill + automatic Y+285 offset (no separate JSON needed).
- Three text layers: Category, Description, Phone.
"""

import logging
import os
import uuid
from pathlib import Path

from django.conf import settings

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

# ── Banner font for Category and Description (Farsi text): Yekan.ttf ──
# Search order: static/fonts/Yekan.ttf (recommended), then root; fallback Persian.ttf.
def _get_persian_font_path():
    base = Path(settings.BASE_DIR)
    for candidate in [
        base / "static" / "fonts" / "Yekan.ttf",
        base / "Yekan.ttf",
        base / "static" / "fonts" / "Persian.ttf",
        base / "Persian.ttf",
    ]:
        if candidate.exists():
            return str(candidate)
    return None


PERSIAN_FONT_PATH = _get_persian_font_path()
assert PERSIAN_FONT_PATH, (
    "FATAL: Banner font not found.\n"
    "Place Yekan.ttf in static/fonts/ (recommended) or in the project root.\n"
    "Fallback: Persian.ttf in static/fonts/ or project root.\n"
    "Image generation for category and message text cannot proceed without it."
)


def _ensure_deps():
    """Import Pillow or fail fast. Returns (Image, ImageDraw, ImageFont, ImageFilter)."""
    try:
        from PIL import Image, ImageDraw, ImageFont, ImageFilter
    except ImportError:  # pragma: no cover - runtime guard
        logger.critical("Pillow not installed; run: pip install Pillow")
        return None, None, None, None
    return Image, ImageDraw, ImageFont, ImageFilter


# ── Nuclear Persian text shaping ─────────────────────────────────────
# No dependency passing, no fallback.  Imports directly; fails loud.

def _shape_persian(text: str, config=None) -> str:
    """
    Process Persian/Arabic text for Pillow rendering.

    When config.use_arabic_reshaper is False, returns raw text (for modern fonts/browsers
    that render RTL correctly without manual reshaping).

    When enabled:
    1. arabic_reshaper with strict Persian configuration:
       connects isolated letters into proper presentation forms.
    2. bidi get_display: reverses visual order for RTL so Pillow
       draws left-to-right correctly.

    Raises ImportError if arabic-reshaper / python-bidi are missing (when reshaping is enabled).
    """
    if not text:
        return ""

    if config is None:
        from core.models import SiteConfiguration
        config = SiteConfiguration.get_config()
    if not getattr(config, 'use_arabic_reshaper', True):
        return text  # Return raw Persian text (for modern fonts/browsers)

    import arabic_reshaper
    from bidi.algorithm import get_display

    configuration = {
        'delete_harakat': True,
        'support_ligatures': True,
        'use_unshaped_instead_of_isolated': False,
    }
    reshaper = arabic_reshaper.ArabicReshaper(configuration=configuration)

    # 1. Reshape (connect letters)
    reshaped_text = reshaper.reshape(text)

    # 2. Bidi (fix Right-to-Left order)
    bidi_text = get_display(reshaped_text)

    return bidi_text


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


def prepare_text(text: str, *, is_phone: bool = False, config=None) -> str:
    """
    Unified "smart text" helper for the image engine.

    For Farsi text (is_phone=False):
        - When config.use_arabic_reshaper: reshape via arabic_reshaper + bidi
        - Otherwise: return raw text
        - Result rendered with Yekan.ttf (banner font)

    For phone numbers (is_phone=True):
        - Convert Persian/Arabic digits to Western (0-9)
        - Skip reshaping and BiDi (phone numbers are always LTR)
        - Result rendered with English font
    """
    if not text:
        return ""
    if is_phone:
        return _normalize_to_western_digits(text).strip()
    return _shape_persian(text, config)


def _resolve_absolute(p: Path) -> Path:
    """Ensure a Path is absolute by resolving against BASE_DIR if needed."""
    if p.is_absolute():
        return p
    return Path(settings.BASE_DIR) / p


def _load_font(ImageFont, size: int):
    """
    Load banner font (Yekan.ttf or Persian.ttf) for Category and Description (Farsi text).
    Phone numbers use _load_english_font.
    """
    font = ImageFont.truetype(PERSIAN_FONT_PATH, size)
    logger.debug("Loaded banner font: %s (size %d)", PERSIAN_FONT_PATH, size)
    return font


def _load_english_font(font_path_override: str | None, ImageFont, size: int):
    """
    Load a Latin/English TrueType font for the Phone layer.

    Search order:
    1. Explicit override path (from coordinates JSON).
    2. Project fonts: media/ad_templates/fonts/English.ttf, Roboto.ttf, etc.
    3. System fonts: Arial, Segoe UI (Windows), DejaVuSans (Linux).
    4. Pillow default.
    """
    paths: list[Path] = []
    base_dir = Path(settings.BASE_DIR)
    media_root = _resolve_absolute(
        Path(getattr(settings, "MEDIA_ROOT", base_dir / "media"))
    )

    if font_path_override:
        p = _resolve_absolute(Path(font_path_override))
        paths.append(p)

    # Project English fonts
    english_font_names = ["English.ttf", "Roboto.ttf", "Inter.ttf", "OpenSans.ttf"]
    search_dirs = [
        media_root / "ad_templates" / "fonts",
        base_dir / "static" / "fonts",
    ]
    for d in search_dirs:
        for name in english_font_names:
            paths.append(d / name)

    # System fonts (common locations)
    import platform
    if platform.system() == "Windows":
        win_fonts = Path("C:/Windows/Fonts")
        for name in ["arial.ttf", "segoeui.ttf", "calibri.ttf", "verdana.ttf"]:
            paths.append(win_fonts / name)
    else:
        # Linux / macOS
        linux_dirs = [
            Path("/usr/share/fonts/truetype/dejavu"),
            Path("/usr/share/fonts/truetype/liberation"),
            Path("/usr/share/fonts/TTF"),
            Path("/System/Library/Fonts"),  # macOS
        ]
        for d in linux_dirs:
            for name in ["DejaVuSans.ttf", "LiberationSans-Regular.ttf", "Arial.ttf"]:
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
        "Place English.ttf or Roboto.ttf in media/ad_templates/fonts/ for best results."
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
    spacing_px: int = 3,
    bold: bool = False,
):
    """
    Draw text character-by-character with custom inter-character spacing.

    Pillow's draw.text() has no letter-spacing option. This function manually
    draws each character and shifts the cursor by (char_advance + spacing_px),
    producing a "tracked" / "kerned" look that's more readable for phone numbers.

    Args:
        draw_obj: PIL ImageDraw instance.
        text: The string to draw (already normalized — e.g. Western digits).
        font: PIL ImageFont (TrueType) instance.
        color: Fill color tuple, e.g. (19, 17, 17).
        x: Left edge of the drawing area.
        y: Top Y position of the text baseline.
        align: "center", "left", or "right" — controls positioning within area_width.
        area_width: Width of the area to align within. If None, uses total text width.
        spacing_px: Extra pixels added between each character (default 3).
        bold: If True, applies stroke for bold simulation.
    """
    if not text:
        return

    stroke_w = max(1, int(font.size * 0.06)) if bold else 0

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
    Word-wrap Persian text to fit within max_width.
    Each line is reshaped individually after wrapping via prepare_text().
    """
    if max_width <= 0:
        return [prepare_text(text)] if text else []

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
            shaped_candidate = prepare_text(candidate_text)
            bbox = draw.textbbox((0, 0), shaped_candidate, font=font)
            width = bbox[2] - bbox[0]

            if width <= max_width:
                current.append(w)
            else:
                if current:
                    line_text = " ".join(current)
                    shaped_line = prepare_text(line_text)
                    lines.append(shaped_line)
                current = [w]

        if current:
            line_text = " ".join(current)
            shaped_line = prepare_text(line_text)
            lines.append(shaped_line)

    return lines


def _get_media_root() -> Path:
    media_root = getattr(settings, "MEDIA_ROOT", None)
    if not media_root:
        media_root = Path(settings.BASE_DIR) / "media"
    return Path(media_root)


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
) -> str | None:
    """
    Generate ad image from an AdTemplate and return filesystem path.

    Layers: Category, Description (body text), Phone.

    Args:
        template_id: Primary key of the AdTemplate.
        category: Category text (e.g. 'فروش ویژه').
        text: Description body text.
        phone: Phone number text.
        background_file: Optional override for background. Can be str path,
                         file-like object, or None to use template's image.
        format_type: 'POST' (1080x1350) or 'STORY' (1080x1920).

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

    # Load post coordinates from template
    coords = default_adtemplate_coordinates()
    try:
        user_coords = tpl.coordinates or {}
        for key, value in user_coords.items():
            if key in coords and isinstance(value, dict):
                coords[key].update({k: v for k, v in value.items() if v is not None})
    except Exception as e:
        logger.warning("create_ad_image: invalid coordinates for template %s: %s", tpl.pk, e)

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

    # For Story format, build the 9:16 canvas with blurred background
    if is_story:
        img = _build_story_canvas(img, Image, ImageFilter)

    draw = ImageDraw.Draw(img)

    def _draw_aligned_line(draw_obj, txt: str, x: int, y: int, font_obj, color, align: str, max_w: int | None = None, bold: bool = False):
        if not txt:
            return
        # Bold simulation: use stroke_width to thicken the text
        stroke_w = max(1, int(font_obj.size * 0.06)) if bold else 0
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

    # ── Category Layer (Yekan.ttf / banner font, reshape+bidi) ──
    c_conf = coords.get("category", {})
    cat_font = _load_font(
        ImageFont,
        _coerce_int(c_conf.get("size"), default=93, minimum=1, maximum=400),
    )
    cat_color = _hex_to_rgb(c_conf.get("color") or "#EEFF00")
    cat_x = _coerce_int(c_conf.get("x"), default=0, minimum=-img.width * 2, maximum=img.width * 2)
    cat_y = _coerce_int(c_conf.get("y"), default=0, minimum=-img.height * 2, maximum=img.height * 2)
    cat_align = (c_conf.get("align") or "center").strip().lower()
    if cat_align not in ("left", "center", "right"):
        cat_align = "center"
    cat_max_w = _coerce_int(c_conf.get("max_width"), default=700, minimum=1, maximum=img.width * 2)
    cat_bold = bool(c_conf.get("bold", True))
    cat_text = prepare_text(category or "", is_phone=False)
    if cat_text:
        _draw_aligned_line(draw, cat_text, cat_x, cat_y, cat_font, cat_color, cat_align, cat_max_w, bold=cat_bold)

    # ── Description Layer (Yekan.ttf / banner font, reshape+bidi, multi-line) ──
    d_conf = coords.get("description", {})
    desc_font = _load_font(
        ImageFont,
        _coerce_int(d_conf.get("size"), default=58, minimum=1, maximum=400),
    )
    desc_color = _hex_to_rgb(d_conf.get("color") or "#FFFFFF")
    desc_x = _coerce_int(d_conf.get("x"), default=0, minimum=-img.width * 2, maximum=img.width * 2)
    desc_y = _coerce_int(d_conf.get("y"), default=0, minimum=-img.height * 2, maximum=img.height * 2)
    max_width = _coerce_int(d_conf.get("max_width"), default=650, minimum=1, maximum=img.width * 2)
    desc_align = (d_conf.get("align") or "center").strip().lower()
    if desc_align not in ("left", "center", "right"):
        desc_align = "center"
    desc_bold = bool(d_conf.get("bold", True))
    desc_stroke_w = max(1, int(desc_font.size * 0.06)) if desc_bold else 0

    wrapped_lines = _wrap_persian_text(draw, text or "", desc_font, max_width)
    for line in wrapped_lines:
        _draw_aligned_line(draw, line, desc_x, desc_y, desc_font, desc_color, desc_align, max_width, bold=desc_bold)
        bbox = draw.textbbox((0, 0), line, font=desc_font, stroke_width=desc_stroke_w)
        desc_y += (bbox[3] - bbox[1]) + 6

    # ── Phone Layer (English font, LTR, Western digits) ──
    p_conf = coords.get("phone", {})
    phone_font = _load_english_font(
        p_conf.get("font_path") or "",
        ImageFont,
        _coerce_int(p_conf.get("size"), default=50, minimum=1, maximum=400),
    )
    phone_color = _hex_to_rgb(p_conf.get("color") or "#131111")
    phone_x = _coerce_int(p_conf.get("x"), default=0, minimum=-img.width * 2, maximum=img.width * 2)
    phone_y = _coerce_int(p_conf.get("y"), default=0, minimum=-img.height * 2, maximum=img.height * 2)
    phone_align = (p_conf.get("align") or "center").strip().lower()
    if phone_align not in ("left", "center", "right"):
        phone_align = "center"
    phone_max_w = _coerce_int(p_conf.get("max_width"), default=550, minimum=1, maximum=img.width * 2)
    phone_bold = bool(p_conf.get("bold", True))
    phone_spacing = _coerce_int(p_conf.get("letter_spacing"), default=1, minimum=0, maximum=20)
    # Phone numbers: normalize digits to Western (0-9) and do NOT apply
    # Persian reshaping/bidi — phone numbers are always LTR.
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
            bold=phone_bold,
        )

    # Draw safety zone guidelines for Story (debug aid — only when DEBUG)
    if is_story and getattr(settings, 'DEBUG', False):
        for zone_y in (STORY_SAFE_TOP, img.height - STORY_SAFE_BOTTOM):
            for x in range(0, img.width, 20):
                draw.line([(x, zone_y), (min(x + 10, img.width), zone_y)], fill=(255, 50, 50, 80), width=1)

    # Save
    media_root = _get_media_root()
    out_dir = media_root / "generated_ads"
    out_dir.mkdir(parents=True, exist_ok=True)

    prefix = "story" if is_story else "ad"
    filename = f"{prefix}_{tpl.pk}_{uuid.uuid4().hex[:8]}.png"
    out_path = out_dir / filename
    try:
        img.save(out_path, format="PNG", optimize=True)
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
    return create_ad_image(template.pk, category_text, description, phone, format_type=format_type)


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
