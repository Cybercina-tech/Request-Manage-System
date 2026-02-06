"""
Iraniu — Global site config in every template.
Reduces DB load by loading config once per request.
"""

from django.conf import settings

from .models import SiteConfiguration


def site_config(request):
    return {'config': SiteConfiguration.get_config()}


def static_version(request):
    """Expose STATIC_VERSION for cache busting in templates."""
    return {'STATIC_VERSION': getattr(settings, 'STATIC_VERSION', '1')}
