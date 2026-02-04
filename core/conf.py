"""
Iranio — Feature flags and config.
OTP logic is wrapped behind ENABLE_OTP; do not activate until ready.
"""

# OTP verification: when True, generate/send/verify codes; when False, skip verification.
ENABLE_OTP = False


# Retention policy hooks (call from cron/tasks; do not delete TelegramUser automatically).
def retention_cleanup_verification_codes(older_than_days=7):
    """Remove expired and used VerificationCodes older than N days. Override as needed."""
    from django.utils import timezone
    from datetime import timedelta
    from core.models import VerificationCode
    threshold = timezone.now() - timedelta(days=older_than_days)
    return VerificationCode.objects.filter(created_at__lt=threshold).delete()
