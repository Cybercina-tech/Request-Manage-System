"""
Passenger WSGI entry — works on cPanel (Passenger) and locally.
Detects environment and uses the correct virtualenv path.
"""
import os
import sys

# Project root (where this file and manage.py live)
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
if PROJECT_DIR not in sys.path:
    sys.path.insert(0, PROJECT_DIR)

# Environment detection: cPanel vs local
def _is_cpanel():
    return PROJECT_DIR.startswith("/home/iraniu")

# Virtualenv paths per environment
VENV_PATHS = {
    "cpanel": "/home/iraniu/virtualenv/Ads_manager/3.12",
    "local": "/Users/siavash/Envs/ads_manager",
}
VENV_ROOT = VENV_PATHS["cpanel"] if _is_cpanel() else VENV_PATHS["local"]

if not os.path.isdir(VENV_ROOT):
    env_name = "cPanel" if _is_cpanel() else "local"
    msg = (
        f"[passenger_wsgi] Virtualenv not found for {env_name}:\n  {VENV_ROOT}\n"
        "Create the venv or update VENV_PATHS in passenger_wsgi.py."
    )
    print(msg, file=sys.stderr)
    raise SystemExit(1)

# Add virtualenv site-packages so Django and dependencies are found
py_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
site_packages = os.path.join(VENV_ROOT, "lib", f"python{py_ver}", "site-packages")
if os.path.isdir(site_packages) and site_packages not in sys.path:
    sys.path.insert(0, site_packages)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "iraniu.settings")
os.environ.setdefault("PASSENGER_APP_ENV", "production")

from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
