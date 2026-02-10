"""
Iraniu — Legacy Telegram webhook by bot_id. Delegates to unified dispatcher.

Primary webhook is in core.views.webhook.TelegramWebhookView at
/telegram/webhook/<uuid:webhook_secret_token>/.
This module keeps the legacy path /telegram/webhook/<int:bot_id>/ for backward compatibility.
"""

import json
import logging
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.core.cache import cache

from core.models import TelegramBot
from core.services.telegram_dispatcher import process_update_payload

logger = logging.getLogger(__name__)

WEBHOOK_RATE_LIMIT = 30
WEBHOOK_RATE_WINDOW = 60


def _user_id_from_body(body):
    from_user = (
        (body.get("message") or {}).get("from")
        or (body.get("edited_message") or {}).get("from")
        or (body.get("callback_query") or {}).get("from")
        or {}
    )
    try:
        return int(from_user.get("id", 0)) or None
    except (TypeError, ValueError):
        return None


@csrf_exempt
@require_http_methods(["POST"])
def telegram_webhook(request, bot_id: int):
    """
    Legacy webhook by bot_id. Verifies optional webhook_secret header, parses JSON,
    rate-limits, calls process_update_payload. Always returns 200.
    """
    bot = TelegramBot.objects.filter(pk=bot_id, is_active=True).first()
    if not bot:
        logger.warning("Webhook: bot_id=%s not found or inactive", bot_id)
        return HttpResponse(status=404)

    if bot.webhook_secret:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").strip()
        if secret != bot.webhook_secret:
            logger.warning("Webhook secret mismatch for bot_id=%s", bot_id)
            return HttpResponse(status=403)

    try:
        body = json.loads(request.body)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning("Webhook invalid JSON bot_id=%s: %s", bot_id, e)
        return HttpResponse(status=400)

    TelegramBot.objects.filter(pk=bot.pk).update(last_webhook_received=timezone.now())
    user_id = _user_id_from_body(body)
    if user_id is not None:
        cache_key = f"telegram_webhook_{bot_id}_{user_id}"
        count = cache.get(cache_key, 0) + 1
        cache.set(cache_key, count, timeout=WEBHOOK_RATE_WINDOW)
        if count > WEBHOOK_RATE_LIMIT:
            logger.warning("Rate limit exceeded bot_id=%s user_id=%s", bot_id, user_id)
            return HttpResponse(status=200)

    try:
        process_update_payload(bot, body)
    except Exception as e:
        logger.exception("Webhook processing failed bot_id=%s: %s", bot_id, e)
    return HttpResponse(status=200)
