"""
Iraniu — Custom template tags and filters.
Persian/Arabic text reshaping for consistent RTL display.
"""

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def persian_display(text):
    """
    Reshape Persian/Arabic text for correct RTL display using arabic_reshaper + bidi.
    Use for user-generated or bilingual content in templates.
    """
    if not text:
        return ''
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
        reshaped = arabic_reshaper.reshape(str(text))
        return mark_safe(get_display(reshaped))
    except (ImportError, Exception):
        return text
