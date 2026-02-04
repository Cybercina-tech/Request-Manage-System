"""
Iranio — Internal submit_ad service. Creates AdRequest directly (no HTTP).
Used by conversation engine when state reaches SUBMITTED.
Maintains audit trail; reuses Django logic (AI moderation, etc.).
"""

import logging
from django.db import transaction
from django.utils import timezone

from core.models import AdRequest, SiteConfiguration, TelegramBot, TelegramUser
from core.services import clean_ad_text, run_ai_moderation

logger = logging.getLogger(__name__)


class SubmitAdService:
    """Internal service to create an ad request (no HTTP)."""

    @staticmethod
    @transaction.atomic
    def submit(
        content: str,
        category: str,
        telegram_user_id: int | None = None,
        telegram_username: str | None = None,
        bot: TelegramBot | None = None,
        raw_telegram_json: dict | None = None,
        user: TelegramUser | None = None,
        contact_snapshot: dict | None = None,
    ) -> AdRequest | None:
        """
        Create AdRequest. Runs AI moderation if enabled.
        Attach user and contact_snapshot at submission time (do not trust live user table for old ads).
        Returns the created AdRequest or None on failure.
        """
        content = clean_ad_text((content or "").strip())
        if not content:
            return None

        config = SiteConfiguration.get_config()
        valid_categories = dict(AdRequest.Category.choices)
        category = category if category in valid_categories else AdRequest.Category.OTHER

        snapshot = contact_snapshot if isinstance(contact_snapshot, dict) else {}

        ad = AdRequest.objects.create(
            content=content,
            category=category,
            status=AdRequest.Status.PENDING_AI,
            telegram_user_id=telegram_user_id,
            telegram_username=(telegram_username or "")[:128],
            bot=bot,
            raw_telegram_json=raw_telegram_json,
            user=user,
            contact_snapshot=snapshot,
        )

        if config.is_ai_enabled:
            approved, reason = run_ai_moderation(ad.content, config)
            ad.status = AdRequest.Status.PENDING_MANUAL
            if not approved and reason:
                ad.ai_suggested_reason = reason[:500]
            ad.save(update_fields=["status", "ai_suggested_reason"])
        else:
            ad.status = AdRequest.Status.PENDING_MANUAL
            ad.save(update_fields=["status"])

        logger.info("AdRequest created: uuid=%s bot=%s user=%s", ad.uuid, bot_id_or_none(bot), telegram_user_id)
        return ad


def bot_id_or_none(bot):
    return bot.pk if bot else None
