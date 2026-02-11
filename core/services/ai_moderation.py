"""
Iraniu — AI moderation and text cleaning. OpenAI integration; safe defaults on failure.
"""

import os
import re
import logging

logger = logging.getLogger(__name__)


def clean_ad_text(text: str | None) -> str:
    """Strip HTML/Markdown for AI and storage."""
    if not text:
        return ''
    text = re.sub(r'<[^>]+>', '', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'\*+([^*]+)\*+', r'\1', text)
    return text.strip()


def validate_ad_content(text: str) -> tuple[bool, str]:
    """
    Validate ad content for no emoji/GIF/sticker (plain text only).
    Returns (is_valid, error_message). Use across forms that accept ad text.
    """
    if not text or not isinstance(text, str):
        return True, ''
    try:
        from core.services.conversation import contains_emoji
        if contains_emoji(text):
            return False, 'Emojis, stickers, and GIFs are not allowed in ad text. Please use plain text only.'
    except ImportError:
        pass
    return True, ''


def run_ai_moderation(content: str, config) -> tuple[bool, str]:
    """
    Send content to OpenAI for moderation.
    Returns (approved: bool, reason: str). Defaults to (True, '') on failure.
    """
    api_key = (os.environ.get("OPENAI_API_KEY") or getattr(config, "openai_api_key", "") or "").strip()
    if not config.is_ai_enabled or not api_key:
        return True, ''

    try:
        import json
        from openai import OpenAI

        client = OpenAI(api_key=api_key, timeout=15.0)
        system = config.ai_system_prompt or (
            'You are a moderator for Iraniu. Check if this ad follows community rules. '
            'Reply with JSON only: {"approved": true or false, "reason": "optional reason"}'
        )
        response = client.chat.completions.create(
            model=config.openai_model or 'gpt-3.5-turbo',
            messages=[
                {'role': 'system', 'content': system},
                {'role': 'user', 'content': content[:4000]},
            ],
            temperature=0.2,
        )
        raw = (response.choices[0].message.content or '').strip()
        json_match = re.search(r'\{[^}]+\}', raw, re.DOTALL)
        if json_match:
            data = json.loads(json_match.group())
            approved = data.get('approved', True)
            reason = data.get('reason', '') or ''
            return bool(approved), str(reason)[:500]
        return True, ''
    except Exception as e:
        logger.exception('AI moderation failed: %s', e)
        return True, ''


def test_openai_connection(api_key: str | None) -> tuple[bool, str]:
    """Test OpenAI API. Returns (success, message)."""
    if not (api_key or '').strip():
        return False, 'No API key provided'
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key.strip(), timeout=10.0)
        client.chat.completions.create(
            model='gpt-3.5-turbo',
            messages=[{'role': 'user', 'content': 'Say OK'}],
            max_tokens=5,
        )
        return True, 'Connected'
    except Exception as e:
        return False, str(e)
