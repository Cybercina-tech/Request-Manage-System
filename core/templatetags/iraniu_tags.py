"""
Iraniu — Custom template tags and filters.
"""

from django import template

register = template.Library()


@register.filter
def persian_display(text):
    """
    Return Persian/Arabic text as-is for template display.
    Image drawing uses raw text (no arabic_reshaper/python-bidi).
    """
    if not text:
        return ''
    return str(text)
