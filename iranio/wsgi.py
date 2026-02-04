"""
WSGI config for Iranio.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'iranio.settings')

application = get_wsgi_application()
