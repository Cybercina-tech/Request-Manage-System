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
