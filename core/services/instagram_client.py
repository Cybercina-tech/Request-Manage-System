"""
Instagram Graph API client using official container + publish flow.

Credentials: IG_USER_ID and FACEBOOK_ACCESS_TOKEN from SiteConfiguration
(instagram_business_id and get_facebook_access_token).
"""

import logging
import time
from typing import Optional

import requests

from django.conf import settings

logger = logging.getLogger(__name__)

GRAPH_API_BASE = "https://graph.facebook.com/v18.0"
REQUEST_TIMEOUT = 15
CONTAINER_STATUS_FINISHED = "FINISHED"
CONTAINER_STATUS_ERROR = "ERROR"
CONTAINER_STATUS_EXPIRED = "EXPIRED"
# Wait up to this many seconds for container to reach FINISHED before publishing
CONTAINER_READY_TIMEOUT = 120
CONTAINER_POLL_INTERVAL = 5


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
        err = data.get("error", {}) or {}
        msg = err.get("message", r.text or f"HTTP {r.status_code}")
        logger.warning("Instagram create_container failed: %s", msg)
        return {
            "success": False,
            "message": msg,
            "http_status": r.status_code,
            "error_data": err,
            "raw_response": data,
        }
    except requests.RequestException as e:
        logger.warning("Instagram create_container request error: %s", e)
        return {"success": False, "message": str(e), "error_data": {"message": str(e)}}


def get_container_status(creation_id: str) -> dict:
    """
    Get the status of a media container via Graph API GET /{container_id}?fields=status_code.

    Returns:
        dict with:
          - success (bool)
          - status_code (str): FINISHED, IN_PROGRESS, ERROR, EXPIRED, PUBLISHED
          - message (str) on error or for logging
    """
    ig_user_id, token = _get_credentials()
    if not ig_user_id or not token:
        return {
            "success": False,
            "status_code": None,
            "message": "Instagram not configured.",
        }
    if not creation_id:
        return {"success": False, "status_code": None, "message": "creation_id is required."}
    url = f"{GRAPH_API_BASE}/{creation_id}"
    params = {"fields": "status_code", "access_token": token}
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        data = r.json() if r.text else {}
        if r.status_code == 200 and "status_code" in data:
            return {
                "success": True,
                "status_code": data.get("status_code"),
                "message": "OK",
            }
        err = data.get("error", {}) or {}
        msg = err.get("message", r.text or f"HTTP {r.status_code}")
        return {
            "success": False,
            "status_code": None,
            "message": msg,
            "http_status": r.status_code,
            "error_data": err,
        }
    except requests.RequestException as e:
        return {"success": False, "status_code": None, "message": str(e), "error_data": {"message": str(e)}}


def wait_for_container_ready(
    creation_id: str,
    timeout_sec: int = CONTAINER_READY_TIMEOUT,
    poll_interval: int = CONTAINER_POLL_INTERVAL,
) -> tuple[bool, str]:
    """
    Poll container status until it is FINISHED (ready to publish) or ERROR/EXPIRED/timeout.

    Returns:
        (True, "") if status_code is FINISHED; (False, error_message) otherwise.
    """
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        result = get_container_status(creation_id)
        if not result.get("success"):
            return False, result.get("message", "Failed to get container status")
        status = result.get("status_code")
        if status == CONTAINER_STATUS_FINISHED:
            return True, ""
        if status in (CONTAINER_STATUS_ERROR, CONTAINER_STATUS_EXPIRED):
            return False, f"Container status {status} (not publishable)"
        # IN_PROGRESS or PUBLISHED (shouldn't happen before we publish) or unknown
        time.sleep(poll_interval)
    return False, "Container did not reach FINISHED within timeout"


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
        err = data.get("error", {}) or {}
        msg = err.get("message", r.text or f"HTTP {r.status_code}")
        logger.warning("Instagram publish_media failed: %s", msg)
        return {
            "success": False,
            "message": msg,
            "http_status": r.status_code,
            "error_data": err,
            "raw_response": data,
        }
    except requests.RequestException as e:
        logger.warning("Instagram publish_media request error: %s", e)
        return {"success": False, "message": str(e), "error_data": {"message": str(e)}}
