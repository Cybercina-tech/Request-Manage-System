"""
Iraniu — Custom template tags and filters.
"""

import json
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def json_pretty(value):
    """Format dict/list as indented JSON for display."""
    if value is None:
        return ''
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return value
    try:
        return mark_safe(json.dumps(value, indent=2, ensure_ascii=False, default=str))
    except (TypeError, ValueError):
        return str(value)


@register.filter
def persian_display(text):
    """
    Return Persian/Arabic text as-is for template display.
    Image drawing uses raw text (no arabic_reshaper/python-bidi).
    """
    if not text:
        return ''
    return str(text)
