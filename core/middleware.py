"""
Iranio — Require authentication for all internal URLs.
Only these are public: /, /login/, /logout/, /api/submit/, /telegram/webhook/*
"""

from django.conf import settings


# Paths (exact or prefix) that anonymous users may access
PUBLIC_PATHS = (
    "/",
    "/login/",
    "/logout/",
    "/api/submit/",
    "/telegram/webhook/",
)


def _is_public(path):
    if not path:
        return True
    path = path.rstrip("/") or "/"
    for allowed in PUBLIC_PATHS:
        allowed_stripped = allowed.rstrip("/") or "/"
        if path == allowed_stripped or path.startswith(allowed_stripped + "/"):
            return True
    return False


class LoginRequiredMiddleware:
    """
    Redirect anonymous users to LOGIN_URL for any non-public path.
    Runs after AuthenticationMiddleware so request.user is available.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not _is_public(request.path):
            if not request.user.is_authenticated:
                from django.contrib.auth.views import redirect_to_login
                return redirect_to_login(request.get_full_path(), login_url=settings.LOGIN_URL)
        return self.get_response(request)
