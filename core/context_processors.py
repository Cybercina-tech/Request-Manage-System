"""
Iranio — Global site config in every template.
Reduces DB load by loading config once per request.
"""

from .models import SiteConfiguration


def site_config(request):
    return {'config': SiteConfiguration.get_config()}
