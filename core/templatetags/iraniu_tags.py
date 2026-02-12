"""
Iraniu — Custom template tags and filters.
Persian/Arabic text reshaping for consistent RTL display.
"""

from django import template

register = template.Library()


@register.filter
def persian_display(text):
    """
    Reshape Persian/Arabic text for correct RTL display using arabic_reshaper + bidi.
    Use for user-generated or bilingual content in templates.

    Uses the same configured reshaper as the image engine for consistency.
    """
    if not text:
        return ''
    try:
        from core.services.image_engine import _shape_persian
        return _shape_persian(str(text))
    except Exception:
        return text
