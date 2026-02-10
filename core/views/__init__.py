"""
Iraniu — Views package. Main app views in main; webhook in webhook.
"""
from core.views.main import *  # noqa: F401, F403
from core.views.webhook import TelegramWebhookView  # noqa: F401
