import os
import sys

############################################
# Project paths on cpanel
############################################

PROJECT_PATH = "/home/iraniu/Ads_manager/public"
VENV_PATH = "/home/iraniu/virtualenv/Ads/manager/3.12"


############################################
# Activate virtual environment
############################################

activate_this = os.path.join(VENV_PATH, "bin", "activate_this.py")

if os.path.exists(activate_this):
    with open(activate_this) as f:
        exec(f.read(), {"__file__": activate_this})
else:
    raise RuntimeError("Virtualenv not found at: " + VENV_PATH)


############################################
# Add project to Python path
############################################

sys.path.insert(0, PROJECT_PATH)


############################################
# Django settings
############################################

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "iraniu.settings")
os.environ.setdefault("PYTHON_EGG_CACHE", "/tmp")


############################################
# Load Django application
############################################

from django.core.wsgi import get_wsgi_application

application = get_wsgi_application()
