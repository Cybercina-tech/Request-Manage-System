"""
Iraniu — Centralized System Log service.

Global utility to record every event, error, and API interaction into SystemLog.
Use log_event() for explicit logging; use @log_exceptions for automatic exception capture.
"""

import functools
import json
import traceback
from typing import Any, Callable, Optional

from core.models import SystemLog, AdRequest


def log_event(
    level: str,
    category: str,
    message: str,
    *,
    ad_request: Optional[AdRequest] = None,
    status_code: Optional[int] = None,
    request_data: Optional[dict] = None,
    response_data: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> SystemLog:
    """
    Create a SystemLog entry. Levels: INFO, WARNING, ERROR, CRITICAL.
    Categories: INSTAGRAM_API, TELEGRAM_BOT, IMAGE_GENERATION, WEBHOOK, SYSTEM_CORE, DATABASE.
    """
    # Ensure we don't log during migrations
    try:
        if not hasattr(SystemLog, 'objects'):
            return None
    except Exception:
        return None

    try:
        return SystemLog.objects.create(
            level=level,
            category=category,
            message=(message or '')[:512],
            ad_request=ad_request,
            status_code=status_code,
            request_data=request_data or {},
            response_data=response_data or {},
            metadata=metadata or {},
        )
    except Exception as e:
        # Avoid recursion; fall back to Python logging
        import logging
        logging.getLogger(__name__).exception("log_event failed: %s", e)
        return None


def _traceback_to_dict(exc: BaseException) -> dict:
    """Convert Python traceback to a structured dict for JSON storage."""
    tb_lines = traceback.format_exception(type(exc), exc, exc.__traceback__)
    return {
        'type': type(exc).__name__,
        'message': str(exc)[:2000],
        'traceback': ''.join(tb_lines)[:8000],
    }


def log_exception(
    exc: BaseException,
    category: str,
    message: str,
    *,
    ad_request: Optional[AdRequest] = None,
    request_data: Optional[dict] = None,
    metadata: Optional[dict] = None,
) -> Optional[SystemLog]:
    """Log an exception with full traceback to SystemLog."""
    response_data = _traceback_to_dict(exc)
    return log_event(
        level=SystemLog.Level.ERROR,
        category=category,
        message=message[:512],
        ad_request=ad_request,
        status_code=500,
        request_data=request_data,
        response_data=response_data,
        metadata=metadata,
    )


def log_exceptions(
    category: str,
    message_prefix: str = 'Error in',
) -> Callable:
    """
    Decorator to catch exceptions in any service and log them to SystemLog.
    Re-raises the exception after logging.
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                ad_req = None
                req_data = {}
                for arg in args:
                    if isinstance(arg, AdRequest):
                        ad_req = arg
                        break
                if 'ad' in kwargs and isinstance(kwargs.get('ad'), AdRequest):
                    ad_req = kwargs['ad']
                if 'ad_request' in kwargs and isinstance(kwargs.get('ad_request'), AdRequest):
                    ad_req = kwargs['ad_request']

                # Build request_data from non-sensitive kwargs
                for k, v in kwargs.items():
                    if k in ('ad', 'ad_request', 'log') or k.startswith('_'):
                        continue
                    try:
                        json.dumps(v)
                        req_data[k] = v
                    except (TypeError, ValueError):
                        req_data[k] = str(v)[:500]

                log_exception(
                    exc,
                    category=category,
                    message=f'{message_prefix} {func.__name__}: {str(exc)[:200]}',
                    ad_request=ad_req,
                    request_data=req_data or None,
                )
                raise

        return wrapper

    return decorator


def parse_facebook_error(response_data: dict) -> dict:
    """
    Parse Facebook/Instagram Graph API error object.
    Captures: message, type, code, error_subcode, fb_trace_id.
    """
    if not isinstance(response_data, dict):
        return {'message': str(response_data), 'type': '', 'code': None, 'error_subcode': None, 'fb_trace_id': ''}
    err = response_data.get('error') or response_data.get('error_data') or response_data
    if not isinstance(err, dict):
        err = {'message': str(err)}
    return {
        'message': err.get('message', ''),
        'type': err.get('type', ''),
        'code': err.get('code'),
        'error_subcode': err.get('error_subcode'),
        'fb_trace_id': err.get('fbtrace_id') or err.get('fb_trace_id', ''),
    }


def parse_telegram_error(response_data: dict) -> dict:
    """
    Parse Telegram API error: ok: false, error_code, description.
    """
    if not isinstance(response_data, dict):
        return {'ok': False, 'error_code': None, 'description': str(response_data)}
    return {
        'ok': response_data.get('ok', True),
        'error_code': response_data.get('error_code'),
        'description': response_data.get('description', ''),
    }
