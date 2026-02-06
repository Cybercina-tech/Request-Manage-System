"""
Iraniu — Telegram Bot API client. Production-ready HTTP client with SSL, retries, error handling.

Features:
- requests.Session with explicit certifi certificate bundle
- Exponential backoff retry logic
- Structured error handling (SSL, network, auth)
- Token masking in logs
- Health check function
- Timeout configuration
"""

import logging
import time
from enum import Enum
from typing import Optional, Tuple, Dict, Any

import certifi
import requests
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from requests.exceptions import SSLError, ConnectionError, Timeout, RequestException

logger = logging.getLogger(__name__)

# Telegram API base URL
TELEGRAM_API_BASE = "https://api.telegram.org"

# Default timeouts (seconds)
DEFAULT_CONNECT_TIMEOUT = 10
DEFAULT_READ_TIMEOUT = 30
DEFAULT_TOTAL_TIMEOUT = 40

# Retry configuration
MAX_RETRIES = 3
BACKOFF_FACTOR = 0.5
RETRY_STATUS_CODES = [500, 502, 503, 504]  # Retry on server errors


class TelegramClientError(Exception):
    """Base exception for Telegram client errors."""
    pass


class TelegramStatus(Enum):
    """Health check status."""
    OK = "ok"
    SSL_ERROR = "ssl_error"
    NETWORK_ERROR = "network_error"
    AUTH_ERROR = "auth_error"
    TIMEOUT_ERROR = "timeout_error"
    UNKNOWN_ERROR = "unknown_error"


def _mask_token(token: str) -> str:
    """Mask bot token for logging. Shows first 4 and last 4 chars."""
    if not token or len(token) < 8:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def _create_session() -> requests.Session:
    """
    Create a requests.Session with proper SSL configuration and retry logic.
    Uses certifi certificate bundle explicitly for macOS compatibility.
    """
    session = requests.Session()
    
    # Configure SSL: use certifi bundle explicitly
    session.verify = certifi.where()
    
    # Configure retry strategy
    retry_strategy = Retry(
        total=MAX_RETRIES,
        backoff_factor=BACKOFF_FACTOR,
        status_forcelist=RETRY_STATUS_CODES,
        allowed_methods=["GET", "POST"],
        raise_on_status=False,  # We handle status codes ourselves
    )
    
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    
    return session


def _make_request(
    session: requests.Session,
    method: str,
    endpoint: str,
    token: str,
    json_data: Optional[Dict[str, Any]] = None,
    params: Optional[Dict[str, Any]] = None,
    timeout: Tuple[int, int] = (DEFAULT_CONNECT_TIMEOUT, DEFAULT_READ_TIMEOUT),
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Make HTTP request to Telegram API.
    
    Args:
        session: requests.Session instance
        method: HTTP method ('GET' or 'POST')
        endpoint: API endpoint (e.g., 'getMe', 'sendMessage')
        token: Bot token (will be masked in logs)
        json_data: Optional JSON payload for POST
        params: Optional query parameters
        timeout: (connect_timeout, read_timeout) tuple
    
    Returns:
        (success: bool, response_data: dict or None, error_message: str or None)
    """
    url = f"{TELEGRAM_API_BASE}/bot{token}/{endpoint}"
    masked_token = _mask_token(token)
    
    try:
        if method.upper() == "GET":
            response = session.get(url, params=params, timeout=timeout)
        elif method.upper() == "POST":
            response = session.post(url, json=json_data, params=params, timeout=timeout)
        else:
            return False, None, f"Unsupported method: {method}"
        
        # Check HTTP status
        if response.status_code == 200:
            data = response.json()
            if data.get("ok"):
                return True, data.get("result"), None
            else:
                error_desc = data.get("description", "Unknown Telegram API error")
                logger.warning(
                    "Telegram API error endpoint=%s token=%s: %s",
                    endpoint,
                    masked_token,
                    error_desc,
                )
                return False, None, error_desc
        
        # Non-200 status
        logger.warning(
            "Telegram API HTTP %s endpoint=%s token=%s: %s",
            response.status_code,
            endpoint,
            masked_token,
            (response.text or "")[:200],
        )
        return False, None, f"HTTP {response.status_code}: {response.text[:200]}"
    
    except SSLError as e:
        logger.error(
            "Telegram SSL error endpoint=%s token=%s: %s",
            endpoint,
            masked_token,
            str(e),
            exc_info=True,
        )
        return False, None, f"SSL error: {str(e)}"
    
    except ConnectionError as e:
        logger.error(
            "Telegram connection error endpoint=%s token=%s: %s",
            endpoint,
            masked_token,
            str(e),
            exc_info=True,
        )
        return False, None, f"Connection error: {str(e)}"
    
    except Timeout as e:
        logger.error(
            "Telegram timeout endpoint=%s token=%s: %s",
            endpoint,
            masked_token,
            str(e),
            exc_info=True,
        )
        return False, None, f"Timeout: {str(e)}"
    
    except RequestException as e:
        logger.error(
            "Telegram request error endpoint=%s token=%s: %s",
            endpoint,
            masked_token,
            str(e),
            exc_info=True,
        )
        return False, None, f"Request error: {str(e)}"
    
    except Exception as e:
        logger.exception(
            "Telegram unexpected error endpoint=%s token=%s",
            endpoint,
            masked_token,
        )
        return False, None, f"Unexpected error: {str(e)}"


def send_message(
    token: str,
    chat_id: int,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
    max_retries: int = MAX_RETRIES,
) -> bool:
    """
    Send a message via Telegram Bot API.
    
    Args:
        token: Bot token
        chat_id: Telegram chat ID
        text: Message text
        reply_markup: Optional inline keyboard (dict)
        max_retries: Maximum retry attempts (default: 3)
    
    Returns:
        True if sent successfully, False otherwise
    """
    if not token or not chat_id:
        logger.warning("send_message: missing token or chat_id")
        return False
    
    session = _create_session()
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    
    for attempt in range(max_retries + 1):
        success, _, error = _make_request(
            session,
            "POST",
            "sendMessage",
            token,
            json_data=payload,
        )
        
        if success:
            return True
        
        if attempt < max_retries:
            wait_time = BACKOFF_FACTOR * (2 ** attempt)
            logger.debug(
                "send_message retry %s/%s after %s seconds: %s",
                attempt + 1,
                max_retries,
                wait_time,
                error,
            )
            time.sleep(wait_time)
    
    logger.warning("send_message failed after %s attempts: %s", max_retries + 1, error)
    return False


def get_me(token: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Call getMe endpoint to verify bot token and get bot info.
    
    Args:
        token: Bot token
    
    Returns:
        (success: bool, bot_info: dict or None, error_message: str or None)
    """
    if not token:
        return False, None, "No token provided"
    
    session = _create_session()
    success, result, error = _make_request(session, "GET", "getMe", token)
    return success, result, error


def get_webhook_info(token: str) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    """
    Get current webhook info.
    
    Args:
        token: Bot token
    
    Returns:
        (success: bool, webhook_info: dict or None, error_message: str or None)
    """
    if not token:
        return False, None, "No token"
    
    session = _create_session()
    success, result, error = _make_request(session, "GET", "getWebhookInfo", token)
    return success, result, error


def set_webhook(
    token: str,
    url: str,
    secret_token: Optional[str] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Set webhook URL.
    
    Args:
        token: Bot token
        url: Webhook URL
        secret_token: Optional secret token for webhook verification
    
    Returns:
        (success: bool, message: str or None)
    """
    if not token:
        return False, "No token"
    
    payload = {"url": url}
    if secret_token:
        payload["secret_token"] = secret_token
    
    session = _create_session()
    success, _, error = _make_request(
        session,
        "POST",
        "setWebhook",
        token,
        json_data=payload,
    )
    
    if success:
        return True, "Webhook set"
    return False, error or "Unknown error"


def delete_webhook(token: str) -> Tuple[bool, Optional[str]]:
    """
    Remove webhook.
    
    Args:
        token: Bot token
    
    Returns:
        (success: bool, message: str or None)
    """
    if not token:
        return False, "No token"
    
    session = _create_session()
    success, _, error = _make_request(
        session,
        "POST",
        "deleteWebhook",
        token,
    )
    
    if success:
        return True, "Webhook removed"
    return False, error or "Unknown error"


def get_updates(
    token: str,
    offset: Optional[int] = None,
    timeout: int = 25,
    limit: int = 100,
) -> Tuple[bool, Optional[list], Optional[str]]:
    """
    Long-polling getUpdates. Use for polling mode; respects Telegram limits.
    
    Args:
        token: Bot token
        offset: Update id offset (return updates with update_id > offset)
        timeout: Long-poll timeout in seconds (1-50)
        limit: Max updates per request (1-100)
    
    Returns:
        (success: bool, list of update dicts or None, error_message: str or None)
    """
    if not token:
        return False, None, "No token"
    timeout = max(1, min(50, timeout))
    limit = max(1, min(100, limit))
    params = {"timeout": timeout, "limit": limit}
    if offset is not None:
        params["offset"] = offset
    session = _create_session()
    success, result, error = _make_request(
        session,
        "GET",
        "getUpdates",
        token,
        params=params,
        timeout=(DEFAULT_CONNECT_TIMEOUT, timeout + 10),
    )
    if success and result is not None:
        return True, result if isinstance(result, list) else [], None
    return False, None, error or "Unknown error"


def check_telegram_health(token: str) -> Tuple[TelegramStatus, Optional[str], Optional[Dict[str, Any]]]:
    """
    Health check: call getMe and return structured status.
    
    Args:
        token: Bot token
    
    Returns:
        (status: TelegramStatus, message: str or None, bot_info: dict or None)
    """
    if not token:
        return TelegramStatus.AUTH_ERROR, "No token provided", None
    
    try:
        success, bot_info, error = get_me(token)
        
        if success and bot_info:
            username = bot_info.get("username", "?")
            return TelegramStatus.OK, f"Connected as @{username}", bot_info
        
        # Determine error type from error message
        if error:
            error_lower = error.lower()
            if "ssl" in error_lower or "certificate" in error_lower:
                return TelegramStatus.SSL_ERROR, error, None
            elif "timeout" in error_lower:
                return TelegramStatus.TIMEOUT_ERROR, error, None
            elif "connection" in error_lower or "network" in error_lower:
                return TelegramStatus.NETWORK_ERROR, error, None
            elif "unauthorized" in error_lower or "invalid" in error_lower or "401" in error:
                return TelegramStatus.AUTH_ERROR, error, None
        
        return TelegramStatus.UNKNOWN_ERROR, error or "Unknown error", None
    
    except Exception as e:
        logger.exception("check_telegram_health unexpected error")
        return TelegramStatus.UNKNOWN_ERROR, f"Unexpected error: {str(e)}", None
