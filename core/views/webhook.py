"""
Iraniu — Telegram webhook endpoint (class-based view).

Path: /telegram/webhook/<uuid:secret>/
- CSRF exempt, POST only.
- Verifies path secret matches TelegramBot.webhook_secret_token and optional
  X-Telegram-Bot-Api-Secret-Token header.
- Parses JSON body and calls unified process_update_payload; returns 200 OK
  immediately so Telegram does not retry on internal errors.
"""

import json
import logging
from django.http import HttpResponse
from django.utils import timezone
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from django.utils.decorators import method_decorator
from django.core.cache import cache

from core.models import TelegramBot
from core.services.telegram_dispatcher import process_update_payload

logger = logging.getLogger(__name__)

WEBHOOK_RATE_LIMIT = 30
WEBHOOK_RATE_WINDOW = 60  # seconds


@method_decorator(csrf_exempt, name="dispatch")
@method_decorator(require_http_methods(["POST"]), name="dispatch")
class TelegramWebhookView(View):
    """
    Webhook gateway by secret UUID. Path: /telegram/webhook/<uuid:secret>/.
    Validates secret (path + optional header), updates last_webhook_received,
    calls process_update_payload. Always returns 200 so Telegram does not retry.
    """

    def post(self, request, secret):
        # secret is the UUID from URL (webhook_secret_token)
        bot = TelegramBot.objects.filter(
            webhook_secret_token=secret,
            is_active=True,
        ).first()
        if not bot:
            logger.warning("Webhook: secret=%s not found or inactive", secret)
            return HttpResponse(status=404)

        secret_header = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").strip()
        if secret_header and str(secret) != secret_header:
            logger.warning("Webhook secret header mismatch for bot_id=%s", bot.pk)
            return HttpResponse(status=403)

        try:
            body = json.loads(request.body)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("Webhook invalid JSON bot_id=%s: %s", bot.pk, e)
            return HttpResponse(status=400)

        TelegramBot.objects.filter(pk=bot.pk).update(last_webhook_received=timezone.now())
        update_id = body.get("update_id", "?")
        logger.info("Webhook received bot_id=%s update_id=%s", bot.pk, update_id)

        user_id = self._user_id_from_body(body)
        if user_id is not None:
            cache_key = f"telegram_webhook_{bot.pk}_{user_id}"
            count = cache.get(cache_key, 0) + 1
            cache.set(cache_key, count, timeout=WEBHOOK_RATE_WINDOW)
            if count > WEBHOOK_RATE_LIMIT:
                logger.warning("Rate limit exceeded bot_id=%s user_id=%s", bot.pk, user_id)
                return HttpResponse(status=200)

        try:
            process_update_payload(bot, body)
        except Exception as e:
            logger.warning("Webhook processing failed bot_id=%s update_id=%s: %s", bot.pk, update_id, e)

        return HttpResponse(status=200)

    @staticmethod
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
