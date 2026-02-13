"""
Iraniu — Custom template tags and filters.
"""

from django import template

register = template.Library()


@register.filter
def persian_display(text):
    """
    Return Persian/Arabic text as-is for template display.
    Reshaping (arabic_reshaper + bidi) is used only for image drawing,
    not for captions or stored data.
    """
    if not text:
        return ''
    return str(text)
