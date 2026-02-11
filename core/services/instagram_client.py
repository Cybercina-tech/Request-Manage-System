"""
Instagram Graph API client using official container + publish flow.

Credentials: IG_USER_ID and FACEBOOK_ACCESS_TOKEN from SiteConfiguration
(instagram_business_id and get_facebook_access_token).
"""

import logging
from typing import Optional

import requests

from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v18.0"
REQUEST_TIMEOUT = 15


def _get_credentials() -> tuple[Optional[str], Optional[str]]:
    """
    Return (IG_USER_ID, FACEBOOK_ACCESS_TOKEN) from SiteConfiguration.
    Uses instagram_business_id and decrypted facebook_access_token_encrypted.
    """
    from core.models import SiteConfiguration

    config = SiteConfiguration.get_config()
    ig_user_id = (getattr(config, "instagram_business_id", None) or "").strip()
    token = (
        config.get_facebook_access_token()
        if hasattr(config, "get_facebook_access_token")
        else ""
    )
    token = (token or "").strip()
    if ig_user_id and token:
        return ig_user_id, token
    return None, None


def create_container(
    image_url: str,
    caption: str = "",
    is_story: bool = False,
) -> dict:
    """
    Create a media container via Graph API POST /{ig_user_id}/media.

    Args:
        image_url: Public URL of the image (must be HTTPS).
        caption: Caption text (Feed only; max 2200 chars; ignored for Story).
        is_story: If True, create as STORIES container (no caption).

    Returns:
        dict with keys:
          - success (bool)
          - creation_id (str, container id) if success
          - message (str) on error or for logging
    """
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return {
            "success": False,
            "message": "Instagram not configured (set IG_USER_ID and FACEBOOK_ACCESS_TOKEN in Site Configuration).",
        }

    if not image_url or not image_url.startswith("http"):
        return {"success": False, "message": "image_url must be a public HTTPS URL."}

    payload = {
        "image_url": image_url,
        "access_token": token,
    }
    if is_story:
        payload["media_type"] = "STORIES"
    else:
        if caption:
            payload["caption"] = (caption or "")[:2200]

    url = f"{GRAPH_API_BASE}/{ig_user_id}/media"
    try:
        r = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        data = r.json() if r.text else {}
        if r.status_code == 200 and data.get("id"):
            return {"success": True, "creation_id": data["id"], "message": "OK"}
        err = data.get("error", {})
        msg = err.get("message", r.text or f"HTTP {r.status_code}")
        logger.warning("Instagram create_container failed: %s", msg)
        return {"success": False, "message": msg}
    except requests.RequestException as e:
        logger.warning("Instagram create_container request error: %s", e)
        return {"success": False, "message": str(e)}


def publish_media(creation_id: str) -> dict:
    """
    Publish a container via Graph API POST /{ig_user_id}/media_publish.

    Args:
        creation_id: Container ID returned by create_container.

    Returns:
        dict with keys:
          - success (bool)
          - id (str, media id) if success
          - message (str) on error
    """
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return {
            "success": False,
            "message": "Instagram not configured (set IG_USER_ID and FACEBOOK_ACCESS_TOKEN in Site Configuration).",
        }

    if not creation_id:
        return {"success": False, "message": "creation_id is required."}

    url = f"{GRAPH_API_BASE}/{ig_user_id}/media_publish"
    payload = {"creation_id": creation_id, "access_token": token}
    try:
        r = requests.post(url, data=payload, timeout=REQUEST_TIMEOUT)
        data = r.json() if r.text else {}
        if r.status_code == 200 and data.get("id"):
            return {"success": True, "id": data["id"], "message": "OK"}
        err = data.get("error", {})
        msg = err.get("message", r.text or f"HTTP {r.status_code}")
        logger.warning("Instagram publish_media failed: %s", msg)
        return {"success": False, "message": msg}
    except requests.RequestException as e:
        logger.warning("Instagram publish_media request error: %s", e)
        return {"success": False, "message": str(e)}
