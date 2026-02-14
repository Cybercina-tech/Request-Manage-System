"""
Iraniu — Custom error handlers (400, 403, 404, 500) with technical diagnostics.
Internal staff tool: show exception/traceback for observability when DEBUG=False.
"""

import traceback

from django.http import HttpResponseBadRequest, HttpResponseForbidden, HttpResponseNotFound, HttpResponseServerError
from django.shortcuts import render


def _error_context(request, error_code, error_title, error_message, technical_details="", alert_class="warning"):
    """Build common context for error templates."""
    return {
        "error_code": error_code,
        "error_title": error_title,
        "error_message": error_message,
        "technical_details": technical_details or "(No technical details available.)",
        "request_path": getattr(request, "path", "") or request.path_info,
        "error_alert_class": alert_class,
    }


def custom_bad_request(request, exception=None):
    """Handler for 400 Bad Request."""
    technical = "The request was malformed or invalid. Check request method, headers, or body."
    if exception is not None:
        technical = (str(exception).strip() or repr(exception)) + "\n\n" + technical
    context = _error_context(
        request,
        error_code=400,
        error_title="Bad Request",
        error_message="The server could not understand or process your request. It may be malformed or missing required data.",
        technical_details=technical,
        alert_class="warning",
    )
    return HttpResponseBadRequest(
        render(request, "errors/error_base.html", context)
    )


def custom_permission_denied(request, exception=None):
    """Handler for 403 Forbidden (CSRF, permission, etc.)."""
    technical = ""
    if exception is not None:
        technical = str(exception).strip() or repr(exception)
    if not technical:
        technical = "Access was denied. Common causes: missing or invalid CSRF token, or user lacks required permission (e.g. not a superuser)."
    # Normalize common cases for clarity
    if "csrf" in technical.lower() or "CSRF" in technical:
        friendly = "Missing or invalid CSRF token. Refresh the page and try again, or ensure the form includes the CSRF token."
    elif "permission" in technical.lower() or "superuser" in technical.lower():
        friendly = "You do not have permission to access this resource. This action may require staff or superuser privileges."
    else:
        friendly = "Access to this resource was denied."
    context = _error_context(
        request,
        error_code=403,
        error_title="Permission Denied",
        error_message=friendly,
        technical_details=technical,
        alert_class="warning",
    )
    return HttpResponseForbidden(
        render(request, "errors/error_base.html", context)
    )


def custom_page_not_found(request, exception=None):
    """Handler for 404 Not Found."""
    technical = ""
    if exception is not None:
        technical = str(exception).strip() or repr(exception)
    path = getattr(request, "path", "") or request.path_info
    if not technical:
        technical = f"The URL path does not exist: {path}"
    else:
        technical = f"Requested path: {path}\n\n{technical}"
    context = _error_context(
        request,
        error_code=404,
        error_title="Page Not Found",
        error_message=f"The page or resource you requested does not exist. The URL may be wrong or the resource was removed.",
        technical_details=technical,
        alert_class="warning",
    )
    return HttpResponseNotFound(
        render(request, "errors/error_base.html", context)
    )


def custom_server_error(request):
    """Handler for 500 Internal Server Error. Traceback comes from ExceptionCaptureMiddleware."""
    technical = ""
    exc_info = getattr(request, "_exception_info", None)
    if exc_info and len(exc_info) == 3:
        try:
            technical = "".join(traceback.format_exception(*exc_info))
        except Exception:
            technical = f"{exc_info[0].__name__}: {exc_info[1]}"
    if not technical:
        technical = "No traceback was captured. Check server logs for the exception."
    context = _error_context(
        request,
        error_code=500,
        error_title="Internal Server Error",
        error_message="An unexpected error occurred on the server. Use the technical diagnostics below to report the issue.",
        technical_details=technical.strip(),
        alert_class="danger",
    )
    return HttpResponseServerError(
        render(request, "errors/error_base.html", context)
    )
