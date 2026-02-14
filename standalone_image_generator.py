#!/usr/bin/env python3
"""
Standalone Image Generator — Persian/Farsi banner with correct RTL text rendering.

This script demonstrates how we generate ad banner images with Persian text,
using arabic_reshaper and python-bidi so that letters connect properly and
direction is right-to-left when drawn with Pillow.

Required packages (install in your environment):
    pip install Pillow
    pip install arabic-reshaper
    pip install python-bidi

Font: Place YekanBakh-Bold.ttf in the same directory as this script,
      or set FONT_PATH below to the full path of the font file.
"""

from pathlib import Path

# -----------------------------------------------------------------------------
# Font path: place YekanBakh-Bold.ttf next to this script, or set absolute path
# -----------------------------------------------------------------------------
FONT_PATH = "YekanBakh-Bold.ttf"

# -----------------------------------------------------------------------------
# Production values (from banner_config.json), hardcoded so no external JSON
# is needed. Canvas: 1080x1080 (Instagram Post square).
# -----------------------------------------------------------------------------
CANVAS_WIDTH = 1080
CANVAS_HEIGHT = 1080
BACKGROUND_COLOR = (28, 28, 38)  # Dark background

# Category (title) layer
CATEGORY_X = 180
CATEGORY_Y = 288
CATEGORY_SIZE = 93
CATEGORY_COLOR = "#EEFF00"
CATEGORY_MAX_WIDTH = 700
CATEGORY_ALIGN = "center"

# Message (body) layer
MESSAGE_X = 215
MESSAGE_Y = 598
MESSAGE_SIZE = 58
MESSAGE_COLOR = "#FFFFFF"
MESSAGE_MAX_WIDTH = 650
MESSAGE_ALIGN = "center"
LINE_SPACING = 6

# Phone layer
PHONE_X = 300
PHONE_Y = 1150
PHONE_SIZE = 48
PHONE_COLOR = "#131111"
PHONE_MAX_WIDTH = 450
PHONE_ALIGN = "center"
PHONE_LETTER_SPACING = 2


def _hex_to_rgb(hex_string: str) -> tuple[int, int, int]:
    """Convert hex color (#RGB or #RRGGBB) to (r, g, b)."""
    s = (hex_string or "#FFFFFF").strip().lstrip("#")
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))


def _resolve_font_path() -> Path:
    """Resolve FONT_PATH: same dir as script first, then current dir."""
    script_dir = Path(__file__).resolve().parent
    for base in (script_dir, Path.cwd()):
        candidate = base / FONT_PATH
        if candidate.exists():
            return candidate
    return script_dir / FONT_PATH


def _reshape_for_drawing(text: str) -> str:
    """
    Prepare Persian/Arabic text for Pillow so it renders correctly.

    Why reshape?
    - In Unicode, Persian/Arabic letters are stored in logical (isolated) form.
    - When drawn with Pillow, they appear disjointed (each letter separate).
    - arabic_reshaper converts them to contextual forms so letters connect
      as they would in a proper RTL script.

    Why bidi (get_display)?
    - Pillow draws text left-to-right by default.
    - Persian/Arabic is right-to-left; without bidi, the visual order is wrong.
    - get_display() applies the Unicode BiDi algorithm and returns the string
      in the correct visual order for display.
    """
    if not text or not text.strip():
        return ""
    import arabic_reshaper
    from bidi.algorithm import get_display

    reshaper = arabic_reshaper.ArabicReshaper(
        configuration={
            "delete_harakat": True,
            "support_ligatures": True,
            "use_unshaped_instead_of_isolated": False,
        }
    )
    reshaped = reshaper.reshape(text.strip())
    return get_display(reshaped)


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    """Split text into lines that fit within max_width (word wrap)."""
    if max_width <= 0:
        return [text] if text else []
    lines = []
    for paragraph in (text or "").split("\n"):
        words = paragraph.split()
        current = []
        for word in words:
            candidate = " ".join(current + [word])
            bbox = draw.textbbox((0, 0), candidate, font=font)
            if bbox[2] - bbox[0] <= max_width:
                current.append(word)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [word]
        if current:
            lines.append(" ".join(current))
    return lines


def _draw_aligned_line(draw, x: int, y: int, text: str, font, color: tuple, align: str, area_width: int) -> None:
    """Draw a single line with optional horizontal alignment (center/left/right)."""
    if not text:
        return
    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    if align == "center":
        start_x = x + max(0, (area_width - text_width) // 2)
    elif align == "right":
        start_x = x
    else:
        start_x = x + max(0, area_width - text_width)
    draw.text((start_x, y), text, fill=color, font=font)


def generate_banner(
    category: str,
    message: str,
    phone_number: str,
    output_filename: str,
) -> str:
    """
    Generate a banner image with category, message, and phone number.

    Persian text is reshaped (connected letters) and reordered (RTL) before
    drawing. The result is saved as a JPEG file.

    Args:
        category: Title/category text (e.g. "تست دسته بندی").
        message: Body text; will be word-wrapped to MESSAGE_MAX_WIDTH.
        phone_number: Phone string (e.g. "+44 7999 123456").
        output_filename: Output path (e.g. "sample_output.jpg").

    Returns:
        Absolute path to the saved image file.
    """
    from PIL import Image, ImageDraw, ImageFont

    font_path = _resolve_font_path()
    if not font_path.exists():
        raise FileNotFoundError(
            f"Font not found: {font_path}. "
            "Place YekanBakh-Bold.ttf in the same directory as this script."
        )

    # Create canvas with solid background
    img = Image.new("RGB", (CANVAS_WIDTH, CANVAS_HEIGHT), BACKGROUND_COLOR)
    draw = ImageDraw.Draw(img)

    # Load fonts (one size per layer; no fake bold — use font's natural weight)
    font_category = ImageFont.truetype(str(font_path), CATEGORY_SIZE)
    font_message = ImageFont.truetype(str(font_path), MESSAGE_SIZE)
    font_phone = ImageFont.truetype(str(font_path), PHONE_SIZE)

    cat_color = _hex_to_rgb(CATEGORY_COLOR)
    msg_color = _hex_to_rgb(MESSAGE_COLOR)
    phone_color = _hex_to_rgb(PHONE_COLOR)

    # ---- Category layer ----
    if category:
        shaped_category = _reshape_for_drawing(category)
        _draw_aligned_line(
            draw,
            CATEGORY_X,
            CATEGORY_Y,
            shaped_category[:200],
            font_category,
            cat_color,
            CATEGORY_ALIGN,
            CATEGORY_MAX_WIDTH,
        )

    # ---- Message layer (multi-line, word-wrapped) ----
    if message:
        shaped_message = _reshape_for_drawing(message)
        msg_lines = _wrap_text(draw, shaped_message, font_message, MESSAGE_MAX_WIDTH)
        y = MESSAGE_Y
        for line in msg_lines:
            _draw_aligned_line(
                draw,
                MESSAGE_X,
                y,
                line[:500],
                font_message,
                msg_color,
                MESSAGE_ALIGN,
                MESSAGE_MAX_WIDTH,
            )
            bbox = draw.textbbox((0, 0), line, font=font_message)
            y += (bbox[3] - bbox[1]) + LINE_SPACING

    # ---- Phone layer ----
    if phone_number:
        # Phone can stay as-is (digits/symbols); optionally reshape if it contains Persian
        phone_text = _reshape_for_drawing(phone_number) if any("\u0600" <= c <= "\u06FF" for c in phone_number) else phone_number.strip()
        _draw_aligned_line(
            draw,
            PHONE_X,
            PHONE_Y,
            phone_text[:60],
            font_phone,
            phone_color,
            PHONE_ALIGN,
            PHONE_MAX_WIDTH,
        )

    # Save to file
    out_path = Path(output_filename).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    save_kw = {"format": "JPEG", "quality": 95, "optimize": True}
    if str(out_path).lower().endswith(".png"):
        save_kw["format"] = "PNG"
        save_kw.pop("quality", None)
    img.save(out_path, **save_kw)
    return str(out_path)


if __name__ == "__main__":
    # Generate a sample image with dummy data to verify the script and font
    sample_path = generate_banner(
        category="تست دسته بندی",
        message="این یک پیام تست برای بررسی خروجی اسکریپت است.",
        phone_number="+44 7999 123456",
        output_filename="sample_output.jpg",
    )
    print(f"Sample image saved: {sample_path}")
