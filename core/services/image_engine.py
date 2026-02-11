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
    """
    Process Persian/Arabic text: Reshape (join letters) + BiDi (RTL direction).
    
    Step A: Reshaping - Convert logical characters to visual shapes (joining letters)
            Supports special Farsi characters: پ، چ، ژ، گ (Pe, Che, Zhe, Gaf)
    Step B: BiDi Algorithm - Fix Right-to-Left direction
    Step C: Returns processed text ready for Pillow rendering
    
    This ensures:
    - Letters are joined correctly (not separate)
    - Text direction is Right-to-Left
    - Special Farsi characters render properly
    """
    if not text:
        return ""
    if not reshaper or not get_display:
        logger.warning("Persian text deps missing; text may render incorrectly")
        return text
    try:
        # Step A: Reshape (join letters into proper visual forms)
        reshaped = reshaper.reshape(text)
        # Step B: BiDi algorithm (fix RTL direction)
        bidi_text = get_display(reshaped)
        return bidi_text
    except Exception as e:
        logger.error("Failed to shape Persian text '%s': %s", text[:50], e)
        return text


def _load_font(base_font_path: str | None, font_path_override: str | None, ImageFont, size: int):
    """
    Load a TrueType font with explicit Persian font fallback.
    
    Order:
      1. font_path in coordinates (absolute or BASE_DIR-relative)
      2. template.font_file
      3. Persian fonts (Persian.ttf, Vazir.ttf, Samim.ttf, Tehran.ttf)
      4. Generic fallback (DejaVuSans)
    
    If no Persian font is found, logs a clear error but returns default font.
    """
    paths = []
    base_dir = Path(settings.BASE_DIR)
    persian_fonts_found = []

    if font_path_override:
        p = Path(font_path_override)
        if not p.is_absolute():
            p = base_dir / font_path_override
        paths.append(p)

    if base_font_path:
        paths.append(Path(base_font_path))

    # Persian fonts (priority order)
    persian_fonts = [
        "static/fonts/Persian.ttf",
        "static/fonts/Vazir.ttf",
        "static/fonts/Samim.ttf",
        "static/fonts/Tehran.ttf",
    ]
    
    for rel in persian_fonts:
        p = base_dir / rel
        paths.append(p)
        if p.exists():
            persian_fonts_found.append(str(p))

    # Generic fallback
    paths.append(base_dir / "static/fonts/DejaVuSans.ttf")

    for p in paths:
        try:
            if p.exists():
                font = ImageFont.truetype(str(p), size)
                # Log which font was loaded (helpful for debugging)
                if any(pf in str(p) for pf in persian_fonts):
                    logger.debug("Loaded Persian font: %s (size %d)", p.name, size)
                return font
        except OSError as e:
            logger.debug("Failed to load font %s: %s", p, e)
            continue

    # No font found - log clear error
    if not persian_fonts_found:
        logger.error(
            "CRITICAL: No Persian font found! Persian text will render incorrectly. "
            "Please ensure Persian.ttf, Vazir.ttf, or Samim.ttf exists in static/fonts/"
        )
    else:
        logger.warning(
            "No valid font found in paths; using Pillow default. "
            "Persian fonts available: %s", ", ".join(persian_fonts_found)
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


def _wrap_persian_text(draw, text: str, font, max_width: int, reshaper, get_display):
    """
    Word-wrap Persian text to fit within max_width.
    
    CRITICAL: Each line must be reshaped INDIVIDUALLY after wrapping.
    Do not reshape the whole block at once, as it breaks line breaks.
    
    Process:
    1. Split text into words
    2. Measure width of candidate line (reshaped for accurate measurement)
    3. Wrap when exceeds max_width
    4. Reshape each final line individually before returning
    """
    if max_width <= 0:
        return [_shape_persian(text, reshaper, get_display)] if text else []

    lines: list[str] = []
    for paragraph in (text or "").split("\n"):
        if not paragraph.strip():
            lines.append("")
            continue
            
        words = paragraph.split()
        current: list[str] = []
        
        for w in words:
            # Build candidate line
            candidate_words = current + [w]
            candidate_text = " ".join(candidate_words)
            
            # Reshape candidate for accurate width measurement
            shaped_candidate = _shape_persian(candidate_text, reshaper, get_display)
            bbox = draw.textbbox((0, 0), shaped_candidate, font=font)
            width = bbox[2] - bbox[0]
            
            if width <= max_width:
                current.append(w)
            else:
                # Line exceeds max_width; save current line and start new one
                if current:
                    # Reshape the completed line individually
                    line_text = " ".join(current)
                    shaped_line = _shape_persian(line_text, reshaper, get_display)
                    lines.append(shaped_line)
                current = [w]
        
        # Add remaining words as final line
        if current:
            line_text = " ".join(current)
            shaped_line = _shape_persian(line_text, reshaper, get_display)
            lines.append(shaped_line)
    
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
    # Step A: Reshape + Step B: BiDi for Category text
    cat_text = _shape_persian(category or "", reshaper, get_display)
    if cat_text:
        # Step C: Draw the processed text
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

    # Multi-line Description: Each line is already reshaped individually by _wrap_persian_text
    wrapped_lines = _wrap_persian_text(draw, text or "", desc_font, max_width, reshaper, get_display)
    for line in wrapped_lines:
        # Line is already reshaped and BiDi-processed by _wrap_persian_text
        # Step C: Draw the processed line
        draw.text((desc_x, desc_y), line, fill=desc_color, font=desc_font)
        bbox = draw.textbbox((0, 0), line, font=desc_font)
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
    # Step A: Reshape + Step B: BiDi for Phone text
    phone_text = _shape_persian(phone or "", reshaper, get_display)
    if phone_text:
        # Step C: Draw the processed text
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

