"""
Passenger WSGI entry for cPanel.
Project root on server: /home/iraniu/Ads_manager/public
Adjust INTERP and PROJECT_DIR if your paths differ.
"""
import os
import sys

# Project directory (where manage.py lives). On server: /home/iraniu/Ads_manager/public
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# Virtualenv: use venv in project dir (e.g. .../public/venv) or set in cPanel Python app
INTERP = os.path.join(PROJECT_DIR, 'venv', 'bin', 'python')
if os.path.exists(INTERP):
    sys.path.insert(0, os.path.dirname(INTERP))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'iraniu.settings')

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
