"""
Iraniu — services package. Business logic lives here; views only handle request/response.

Re-exports for backward compatibility:
  from core.services import clean_ad_text, run_ai_moderation, send_telegram_message_via_bot, ...
Submodules:
  from core.services.dashboard import get_dashboard_context, get_pulse_data
  from core.services.ad_actions import approve_one_ad, reject_one_ad
  from core.services.ai_moderation import ...
  from core.services.telegram import ...
  from core.services.users import ...
  from core.services.otp import ...
"""

from .ai_moderation import clean_ad_text, run_ai_moderation, test_openai_connection, validate_ad_content
from .telegram import (
    delete_webhook,
    edit_message_text_via_bot,
    answer_callback_query_via_bot,
    get_webhook_info,
    send_telegram_message,
    send_telegram_message_via_bot,
    send_telegram_rejection_with_button,
    send_telegram_rejection_with_button_via_bot,
    set_webhook,
    test_telegram_connection,
)

__all__ = [
    'clean_ad_text',
    'run_ai_moderation',
    'test_openai_connection',
    'validate_ad_content',
    'send_telegram_message',
    'send_telegram_message_via_bot',
    'edit_message_text_via_bot',
    'answer_callback_query_via_bot',
    'send_telegram_rejection_with_button',
    'send_telegram_rejection_with_button_via_bot',
    'test_telegram_connection',
    'get_webhook_info',
    'set_webhook',
    'delete_webhook',
]
