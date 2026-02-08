"""
Iraniu — Shared view helpers. Request parsing only; no business logic.
"""

import json


def parse_request_json(request):
    """
    Parse JSON from request body. Returns dict or empty dict on failure.
    Does not log or raise; views should validate required keys.
    """
    if not request.body:
        return {}
    if request.content_type and "application/json" in request.content_type:
        try:
            return json.loads(request.body)
        except (ValueError, TypeError):
            return {}
    return {}


def get_request_payload(request):
    """
    Return POST data as a single dict. Use this when a view can receive
    either JSON or form-encoded body to avoid RawPostDataException.

    - If Content-Type is application/json: reads request.body once and parses JSON.
    - Otherwise: returns request.POST.dict() (form data).

    Do not mix request.body and request.POST in the same view; use this helper
    so the body stream is only consumed once.
    """
    content_type = (request.content_type or "").lower()
    if "application/json" in content_type:
        try:
            return json.loads(request.body) if request.body else {}
        except (ValueError, TypeError):
            return {}
    return request.POST.dict()
