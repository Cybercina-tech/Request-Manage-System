"""
Iraniu — Core models.

- SiteConfiguration: singleton (pk=1) for AI, messaging, legacy Telegram config.
- TelegramUser: one per Telegram user; contact and verification state.
- VerificationCode: OTP codes (hashed); used when ENABLE_OTP is True.
- AdRequest: ad submission; status flow pending_ai → pending_manual → approved/rejected.
- TelegramBot: multi-bot; token encrypted; webhook optional.
- TelegramSession: per-user per-bot conversation state.
- TelegramMessageLog: optional message history for audit.
- AdminProfile: staff admin with Telegram ID for new-request notifications.
"""

import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone

from .encryption import decrypt_token, encrypt_token, mask_token
from .validators import validate_ad_content_length, validate_ad_content_persian


def default_workflow_stages():
    """Default CRM workflow pipeline."""
    return [
        {"key": "pending", "label": "Pending", "enabled": True},
        {"key": "processing", "label": "Processing", "enabled": True},
        {"key": "published", "label": "Published", "enabled": True},
        {"key": "archived", "label": "Archived", "enabled": True},
    ]


class SiteConfiguration(models.Model):
    """
    Singleton-style global configuration.
    API keys, toggles, and system behavior.
    """
    # AI
    is_ai_enabled = models.BooleanField(default=False)
    openai_api_key = models.CharField(max_length=255, blank=True)
    openai_model = models.CharField(
        max_length=64,
        default='gpt-3.5-turbo',
        help_text='e.g. gpt-4o, gpt-3.5-turbo'
    )
    ai_system_prompt = models.TextField(
        blank=True,
        default='You are a moderator for Iraniu. Check if this ad follows community rules. '
                'Reply with JSON: {"approved": true/false, "reason": "optional reason"}'
    )
    # Telegram
    telegram_bot_token = models.CharField(max_length=255, blank=True)
    telegram_bot_username = models.CharField(max_length=64, blank=True, help_text='Bot username without @, for Edit & Resubmit link')
    telegram_webhook_url = models.URLField(blank=True)
    use_webhook = models.BooleanField(default=False)
    # Messaging (client-facing: no internal IDs; friendly tone + emoji)
    approval_message_template = models.TextField(
        default='🎉 Your ad has been approved! Thank you for using Iraniu. 🙏',
        help_text='Sent to user on approval. Use {ad_id} only if you need it (not shown by default).',
    )
    rejection_message_template = models.TextField(
        default='Your ad was not approved. Reason: {reason}. Ad ID: {ad_id}.'
    )
    submission_ack_message = models.TextField(
        blank=True,
        default='Your broadcast is currently under AI scrutiny. We\'ll notify you the moment it goes live.'
    )
    # Production URL for webhook (e.g. https://iraniu.ir). Required for Telegram webhook; HTTPS only.
    production_base_url = models.URLField(
        blank=True,
        max_length=512,
        help_text='Base URL of the site (e.g. https://iraniu.ir). Used to build webhook URL.',
    )
    # Outbound API: when an ad is approved/generated, POST JSON to this URL (API Settings / API Management).
    external_webhook_url = models.URLField(
        blank=True,
        max_length=512,
        help_text='URL to receive ad payload when an ad is approved (POST JSON). Leave empty to disable.',
    )
    enable_webhook_sync = models.BooleanField(
        default=False,
        help_text='When ON, approved ads are sent to External Webhook URL as JSON.',
    )
    webhook_secret_key = models.CharField(
        max_length=255,
        blank=True,
        help_text='Secret sent as X-Webhook-Secret header for webhook verification at destination.',
    )
    # Default Telegram channel (singleton): used by distribute_ad when is_channel_active.
    telegram_channel_id = models.CharField(
        max_length=32,
        blank=True,
        help_text='Telegram Chat ID for the default ads channel (e.g. -100123456789).',
    )
    telegram_channel_title = models.CharField(
        max_length=128,
        blank=True,
        help_text='Display name for the default channel (e.g. "Live Ads Channel").',
    )
    telegram_channel_handle = models.CharField(
        max_length=64,
        blank=True,
        help_text='Channel handle shown in captions (e.g. @YourChannel).',
    )
    is_channel_active = models.BooleanField(
        default=False,
        help_text='When True, approved ads are posted to the channel above using the bot below.',
    )
    default_telegram_bot = models.ForeignKey(
        'TelegramBot',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='+',
        help_text='Bot used to post to the default channel above (must have admin rights in that channel).',
    )
    # Instagram Business (for Post to Feed/Story; fallback if no InstagramConfiguration)
    is_instagram_enabled = models.BooleanField(
        default=False,
        help_text='Auto-managed: True when all required Instagram fields are filled.',
    )
    instagram_app_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        default='',
        help_text='Facebook App ID (Meta for Developers).',
    )
    instagram_app_secret_encrypted = models.TextField(
        null=True,
        blank=True,
        default='',
        help_text='Facebook App Secret (encrypted at rest).',
    )
    instagram_business_id = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        default='',
        help_text='Instagram Graph API user ID (Business account linked to Facebook Page).',
    )
    facebook_access_token_encrypted = models.TextField(
        null=True,
        blank=True,
        default='',
        help_text='Long-lived Facebook/Instagram access token (encrypted at rest).',
    )
    instagram_token_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text='When the current long-lived access token expires (auto-set on OAuth flow).',
    )
    instagram_oauth_state = models.CharField(
        max_length=128,
        null=True,
        blank=True,
        default='',
        help_text='CSRF state token for in-progress Instagram OAuth flow.',
    )
    # Persian/Arabic text shaping
    use_arabic_reshaper = models.BooleanField(
        default=True,
        help_text='When ON, use arabic_reshaper+bidi for Persian text in images and templates. Turn OFF if text looks garbled (modern fonts/browsers often render RTL correctly without it).',
    )
    # UI — Professional Light Theme (default) or Dark
    theme_preference = models.CharField(
        max_length=16,
        choices=[('light', 'Light'), ('dark', 'Dark')],
        default='light',
        help_text='Global UI theme: Light (easy on eyes) or Dark.',
    )
    # CRM defaults
    default_font = models.CharField(
        max_length=255,
        blank=True,
        default='',
        help_text='Default font for generated content and UI defaults (path/name).',
    )
    default_watermark_opacity = models.PositiveSmallIntegerField(
        default=60,
        help_text='Default watermark opacity percentage (0-100).',
    )
    default_watermark = models.ImageField(
        upload_to='settings/watermarks/',
        blank=True,
        null=True,
        help_text='Default watermark image for generated ads.',
    )
    default_primary_color = models.CharField(
        max_length=18,
        default='#2b8adf',
        blank=True,
        help_text='Default primary color (hex) for new ads.',
    )
    default_secondary_color = models.CharField(
        max_length=18,
        default='#3fb98f',
        blank=True,
        help_text='Default secondary color (hex) for new ads.',
    )
    default_accent_color = models.CharField(
        max_length=18,
        default='#39a0f1',
        blank=True,
        help_text='Default accent color (hex) for new ads.',
    )
    # Workflow & automation
    workflow_stages = models.JSONField(
        default=default_workflow_stages,
        blank=True,
        help_text='Ordered workflow stages: [{"key","label","enabled"}].',
    )
    auto_responder_message = models.TextField(
        blank=True,
        default='Welcome to Iraniu. Please send your ad details to continue.',
        help_text='Automatic welcome message sent when a user starts the bot.',
    )
    auto_reply_comments = models.BooleanField(
        default=False,
        help_text='Enable automatic replies to comments.',
    )
    auto_reply_dms = models.BooleanField(
        default=False,
        help_text='Enable automatic replies to DMs.',
    )
    # Data management
    retention_policy = models.CharField(
        max_length=16,
        choices=[('1w', '1 Week'), ('1m', '1 Month'), ('forever', 'Forever')],
        default='1m',
        help_text='How long generated images are retained.',
    )
    cleanup_retention_days = models.PositiveIntegerField(
        default=7,
        help_text='Number of days to keep generated images before manual cleanup (min 1).',
    )
    # Maintenance
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Site Configuration'
        verbose_name_plural = 'Site Configuration'
        permissions = (
            ('can_edit_settings', 'Can edit restricted CRM settings tabs'),
        )

    def clean(self):
        from django.core.exceptions import ValidationError
        url = (self.production_base_url or "").strip()
        if url and not url.startswith("https://"):
            raise ValidationError({
                "production_base_url": "Must be HTTPS (e.g. https://iraniu.ir). Required for Telegram webhook.",
            })

    def _sync_instagram_enabled(self):
        """Auto-toggle is_instagram_enabled based on required Instagram fields."""
        required_fields_filled = all([
            (self.instagram_app_id or '').strip(),
            (self.instagram_app_secret_encrypted or '').strip(),
            (self.instagram_business_id or '').strip(),
            (self.facebook_access_token_encrypted or '').strip(),
        ])
        self.is_instagram_enabled = required_fields_filled

    def save(self, *args, **kwargs):
        if not self.pk and SiteConfiguration.objects.exists():
            return SiteConfiguration.objects.first()
        # Auto-toggle Instagram status before saving
        self._sync_instagram_enabled()
        result = super().save(*args, **kwargs)
        try:
            from django.core.cache import cache
            cache.delete('site_config_singleton')
        except Exception:
            pass
        return result

    @classmethod
    def get_config(cls):
        """
        Return the singleton SiteConfiguration, creating it with safe defaults
        if it doesn't exist. Wrapped in try/except to never raise IntegrityError.
        """
        try:
            obj, _ = cls.objects.get_or_create(pk=1, defaults={
                'is_instagram_enabled': False,
                'instagram_app_id': '',
                'instagram_app_secret_encrypted': '',
                'instagram_business_id': '',
                'facebook_access_token_encrypted': '',
                'instagram_oauth_state': '',
            })
        except Exception:
            # Fallback: try to fetch existing, or create with minimal fields
            obj = cls.objects.filter(pk=1).first()
            if obj is None:
                obj = cls(pk=1)
                obj.save()
        return obj

    def get_instagram_app_secret(self) -> str:
        """Return decrypted Instagram App Secret."""
        return decrypt_token(self.instagram_app_secret_encrypted)

    def set_instagram_app_secret(self, plain_secret: str):
        self.instagram_app_secret_encrypted = encrypt_token((plain_secret or '').strip())

    def get_facebook_access_token(self) -> str:
        """Return decrypted Facebook access token for Instagram API."""
        return decrypt_token(self.facebook_access_token_encrypted)

    def set_facebook_access_token(self, plain_token: str):
        self.facebook_access_token_encrypted = encrypt_token((plain_token or '').strip())


def default_active_errors():
    """Default for SystemStatus.active_errors (list of error strings)."""
    return []


class SystemStatus(models.Model):
    """
    Singleton-style system watchdog: heartbeat from runbots worker,
    bot active flag, and active error messages (e.g. "Instagram Token Expired").
    """
    last_heartbeat = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text='Last heartbeat from runbots worker; if older than 2 min, worker is OFFLINE.',
    )
    is_bot_active = models.BooleanField(
        default=False,
        help_text='True when runbots process is running and sending heartbeats.',
    )
    active_errors = models.JSONField(
        default=default_active_errors,
        blank=True,
        help_text='List of current error messages, e.g. ["Instagram Token Expired"].',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'System Status'
        verbose_name_plural = 'System Status'

    @classmethod
    def get_status(cls):
        obj, _ = cls.objects.get_or_create(pk=1, defaults={'active_errors': []})
        return obj

    def add_active_error(self, message: str):
        if not message or not isinstance(self.active_errors, list):
            self.active_errors = list(self.active_errors or [])
        msg = (message or '').strip()[:512]
        if msg and msg not in self.active_errors:
            self.active_errors.append(msg)
            self.save(update_fields=['active_errors', 'updated_at'])

    def clear_active_error(self, message: str):
        if isinstance(self.active_errors, list) and message:
            self.active_errors = [m for m in self.active_errors if m != message.strip()]
            self.save(update_fields=['active_errors', 'updated_at'])


class Notification(models.Model):
    """Internal system-wide notification (Success, Info, Warning, Error). Supports RTL/Persian."""
    class Level(models.TextChoices):
        SUCCESS = 'success', 'Success'
        INFO = 'info', 'Info'
        WARNING = 'warning', 'Warning'
        ERROR = 'error', 'Error'

    level = models.CharField(max_length=16, choices=Level.choices, db_index=True)
    message = models.TextField(help_text='Notification text (supports Persian/RTL).')
    link = models.URLField(blank=True, help_text='Optional URL to fix the issue.')
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Notification'
        verbose_name_plural = 'Notifications'

    def __str__(self):
        return f'{self.get_level_display()}: {self.message[:50]}'


class TelegramUser(models.Model):
    """
    One row per Telegram user. Updated on every interaction.
    Never delete automatically.
    """

    telegram_user_id = models.BigIntegerField(unique=True, db_index=True)
    username = models.CharField(max_length=128, null=True, blank=True)
    first_name = models.CharField(max_length=128, null=True, blank=True)
    last_name = models.CharField(max_length=128, null=True, blank=True)
    language_code = models.CharField(max_length=8, null=True, blank=True)
    is_bot = models.BooleanField(default=False)

    phone_number = models.CharField(max_length=20, null=True, blank=True)  # E.164, max 15 digits
    email = models.EmailField(null=True, blank=True)

    phone_verified = models.BooleanField(default=False)
    email_verified = models.BooleanField(default=False)

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    last_seen = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ['-last_seen', '-created_at']
        verbose_name = 'Telegram User'
        verbose_name_plural = 'Telegram Users'

    def __str__(self):
        return f"@{self.username or '?'} ({self.telegram_user_id})"


class VerificationCode(models.Model):
    """OTP codes (hashed). Used only when ENABLE_OTP is True."""

    class Channel(models.TextChoices):
        EMAIL = 'email', 'Email'
        PHONE = 'phone', 'Phone'

    user = models.ForeignKey(
        TelegramUser,
        on_delete=models.CASCADE,
        related_name='verification_codes',
    )
    channel = models.CharField(max_length=16, choices=Channel.choices)
    code_hashed = models.CharField(max_length=128)  # hashed, not plain
    expires_at = models.DateTimeField(db_index=True)
    used = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Verification Code'
        verbose_name_plural = 'Verification Codes'

    def __str__(self):
        return f"{self.user_id} {self.channel} expires={self.expires_at}"


class Category(models.Model):
    """Dynamic category for ad requests. Replaces hardcoded choices."""

    name = models.CharField(max_length=64, help_text='Display name (e.g. Real Estate, Job)')
    name_fa = models.CharField(
        max_length=100,
        blank=True,
        default='',
        help_text='Persian name for image generation (e.g. فروش ویژه). Falls back to name if empty.',
    )
    slug = models.SlugField(max_length=64, unique=True, help_text='URL-safe identifier; used as callback_data in bot')
    color = models.CharField(max_length=16, default='#7C4DFF', help_text='Hex color for badges (e.g. #7C4DFF)')
    icon = models.CharField(max_length=64, blank=True, help_text='Optional: Lucide/icon name')
    is_active = models.BooleanField(default=True, db_index=True)
    order = models.PositiveIntegerField(default=0, help_text='Sort order (lower first)')

    class Meta:
        ordering = ['order', 'name']
        verbose_name = 'Category'
        verbose_name_plural = 'Categories'

    def __str__(self):
        return self.name_fa or self.name

    @property
    def display_name_fa(self):
        """Persian display name with fallback to English name."""
        return self.name_fa or self.name

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            from django.utils.text import slugify
            self.slug = slugify(self.name)[:64]
        super().save(*args, **kwargs)


class AdRequest(models.Model):
    """Core entity: ad submission with lifecycle states."""

    class Status(models.TextChoices):
        PENDING_AI = 'pending_ai', 'Pending AI'
        PENDING_MANUAL = 'pending_manual', 'Pending Manual'
        NEEDS_REVISION = 'needs_revision', 'Needs Revision'
        APPROVED = 'approved', 'Approved'
        REJECTED = 'rejected', 'Rejected'
        EXPIRED = 'expired', 'Expired'
        SOLVED = 'solved', 'Solved'

    uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True)
    bot = models.ForeignKey(
        'TelegramBot',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ad_requests',
        help_text='Bot through which ad was submitted (if via Telegram)'
    )
    user = models.ForeignKey(
        TelegramUser,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ad_requests',
        help_text='Telegram user profile (if via Telegram)'
    )
    contact_snapshot = models.JSONField(
        default=dict,
        blank=True,
        help_text='Contact at submission time: phone, email, verified_phone, verified_email'
    )
    category = models.ForeignKey(
        Category,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='ad_requests',
        help_text='Ad category (dynamic)',
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING_AI,
        db_index=True
    )
    content = models.TextField(
        validators=[validate_ad_content_length, validate_ad_content_persian]
    )
    rejection_reason = models.TextField(blank=True)
    ai_suggested_reason = models.TextField(blank=True)
    telegram_user_id = models.BigIntegerField(null=True, blank=True)
    telegram_username = models.CharField(max_length=128, blank=True)
    raw_telegram_json = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    submitted_via_api_client = models.ForeignKey(
        'ApiClient',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='submitted_ads',
        help_text='If set, ad was submitted via Partner API by this client',
    )
    # Generated images: strictly separate for Feed (post) vs Story (9:16)
    generated_image = models.ImageField(
        upload_to='generated_ads/',
        blank=True,
        null=True,
        help_text='Generated image for Feed post (1:1 or 4:5). Used for Telegram channel and Instagram Feed.',
    )
    generated_story_image = models.ImageField(
        upload_to='generated_stories/',
        blank=True,
        null=True,
        help_text='Generated image for Instagram Story (9:16). Never mixed with Feed.',
    )
    # Instagram publish state (per target)
    instagram_post_id = models.CharField(max_length=64, null=True, blank=True)
    instagram_story_id = models.CharField(max_length=64, null=True, blank=True)
    is_instagram_published = models.BooleanField(
        default=False,
        help_text='True when at least one of Feed or Story was successfully published to Instagram.',
    )
    # Instagram queue: when enable_instagram_queue is ON, ads are marked queued and sent by process_instagram_queue
    instagram_queue_status = models.CharField(
        max_length=16,
        blank=True,
        default='',
        db_index=True,
        choices=[
            ('', '—'),
            ('queued', 'Queued'),
            ('sent', 'Sent'),
            ('failed', 'Failed'),
        ],
        help_text='Queued: waiting for scheduler. Sent/Failed: set by queue processor.',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['created_at']),
            models.Index(fields=['category', 'status']),
        ]

    def __str__(self):
        return f'{self.get_category_display()} — {self.status} ({self.uuid})'

    def get_category_display(self):
        """Display name for category (compatible with old get_category_display for choices)."""
        return self.category.name if self.category else 'Other'

    def get_category_display_fa(self):
        """Persian display name for category (name_fa or name). Use in UI/preview."""
        if self.category:
            return getattr(self.category, 'name_fa', '') or self.category.name
        return 'سایر'

    def get_category_color(self):
        """Hex color for badge styling."""
        return (self.category.color or '#7C4DFF') if self.category else '#7C4DFF'

    def get_absolute_feed_image_url(self) -> str | None:
        """
        Return the absolute public URL for the Feed image (e.g. for Instagram/Telegram).
        Example: https://request.iraniu.uk/media/generated_ads/ads_123.png
        No login required; /media/ is served publicly (read-only).
        """
        from core.services.instagram_api import get_absolute_media_url
        return get_absolute_media_url(self.generated_image)

    def get_absolute_story_image_url(self) -> str | None:
        """
        Return the absolute public URL for the Story image (e.g. for Instagram Story).
        Example: https://request.iraniu.uk/media/generated_stories/story_123.png
        No login required; /media/ is served publicly (read-only).
        """
        from core.services.instagram_api import get_absolute_media_url
        return get_absolute_media_url(self.generated_story_image)

    def generate_images(self) -> bool:
        """
        Generate Feed and Story images for this ad using banner_config.json and
        the active AdTemplate. Uses YekanBakh-Bold.ttf and no pseudo-bold stroke.
        Returns True if both images were generated (or already present).
        """
        from core.services.image_engine import ensure_feed_image, ensure_story_image
        feed_ok = ensure_feed_image(self)
        story_ok = ensure_story_image(self)
        return feed_ok and story_ok


class TelegramBot(models.Model):
    """
    Multi-bot support: one project can manage multiple bots.
    Fields: mode (Webhook/Polling), bot_token_encrypted (token at rest), is_active, environment (PROD/DEV).
    Only active bots for the current ENVIRONMENT are started. Token is validated via getMe; use Reset Bot Connection to clear webhook.
    """

    class Status(models.TextChoices):
        ONLINE = 'online', 'Online'
        OFFLINE = 'offline', 'Offline'
        ERROR = 'error', 'Error'

    class Mode(models.TextChoices):
        WEBHOOK = 'webhook', 'Webhook'
        POLLING = 'polling', 'Polling'

    class RequestedAction(models.TextChoices):
        START = 'start', 'Start'
        STOP = 'stop', 'Stop'
        RESTART = 'restart', 'Restart'

    class Environment(models.TextChoices):
        PROD = 'PROD', 'Production'
        DEV = 'DEV', 'Development'

    name = models.CharField(max_length=128, help_text='Human-readable name (e.g. Production Bot, Dev Bot)')
    bot_token_encrypted = models.TextField(blank=True)  # Encrypted at rest; never expose in templates
    username = models.CharField(max_length=64, blank=True, help_text='Bot username without @')
    is_active = models.BooleanField(default=True)
    environment = models.CharField(
        max_length=8,
        choices=Environment.choices,
        default=Environment.PROD,
        db_index=True,
        help_text='PROD: cPanel/live. DEV: local/testing. Only bots matching settings.ENVIRONMENT are started.',
    )
    is_default = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Only one bot can be default. Used as the system bot when no other is specified.',
    )
    mode = models.CharField(
        max_length=16,
        choices=Mode.choices,
        default=Mode.WEBHOOK,
        help_text='Webhook: updates via HTTP. Polling: runbots worker fetches getUpdates.',
    )
    webhook_url = models.URLField(blank=True)
    webhook_secret = models.CharField(max_length=64, blank=True, help_text='Secret for webhook verification')
    webhook_secret_token = models.UUIDField(
        default=uuid.uuid4,
        editable=False,
        unique=True,
        db_index=True,
        help_text='Secret UUID in webhook path; only Telegram and this app know the URL.',
    )
    last_heartbeat = models.DateTimeField(null=True, blank=True)
    last_webhook_received = models.DateTimeField(
        null=True,
        blank=True,
        help_text='Last time Telegram sent an update to this bot (webhook health).',
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OFFLINE,
        db_index=True
    )
    worker_pid = models.PositiveIntegerField(null=True, blank=True, help_text='PID of polling worker when running')
    worker_started_at = models.DateTimeField(null=True, blank=True)
    current_pid = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text='PID of the runbots supervisor process when started from UI (for Stop).',
    )
    is_running = models.BooleanField(
        default=False,
        help_text='True when a runbots process for this bot was started from the panel.',
    )
    last_error = models.TextField(blank=True, help_text='Last error message; cleared on success')
    requested_action = models.CharField(
        max_length=16,
        choices=RequestedAction.choices,
        blank=True,
        null=True,
        help_text='Start/Stop/Restart requested by admin; runbots applies it.',
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Telegram Bot'
        verbose_name_plural = 'Telegram Bots'

    def __str__(self):
        return f'{self.name} (@{self.username or "?"})'

    def get_decrypted_token(self):
        return decrypt_token(self.bot_token_encrypted)

    def set_token(self, plain_token: str):
        self.bot_token_encrypted = encrypt_token((plain_token or '').strip())

    def get_masked_token(self):
        return mask_token(self.get_decrypted_token())

    def save(self, *args, **kwargs):
        if not self.webhook_secret_token:
            self.webhook_secret_token = uuid.uuid4()
        if self.is_default:
            TelegramBot.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class TelegramChannel(models.Model):
    """
    Telegram channel (group/supergroup) for automated ad posts.
    Can be linked to Site Configuration (site_config) to manage from Settings; bot_connection must have admin rights.
    """

    site_config = models.ForeignKey(
        'SiteConfiguration',
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name='telegram_channels',
        help_text='Link to Site Configuration to manage this channel from Settings.',
    )
    title = models.CharField(
        max_length=128,
        help_text='Internal name for this channel (e.g. "Main Ads Channel")',
    )
    channel_id = models.CharField(
        max_length=32,
        help_text='Telegram Chat ID (e.g. -100123456789 for supergroups)',
    )
    is_active = models.BooleanField(
        default=True,
        db_index=True,
        help_text='When disabled, this channel is excluded from automated posts.',
    )
    is_default = models.BooleanField(
        default=False,
        db_index=True,
        help_text='Primary channel for automated ads (when not using Site Configuration default).',
    )
    bot_connection = models.ForeignKey(
        TelegramBot,
        on_delete=models.CASCADE,
        related_name='telegram_channels',
        help_text='Bot that has admin rights in this channel (used to send messages).',
    )
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['title']
        verbose_name = 'Telegram Channel'
        verbose_name_plural = 'Channel Manager'

    def __str__(self):
        return f'{self.title} ({self.channel_id})'

    def save(self, *args, **kwargs):
        if self.is_default:
            TelegramChannel.objects.filter(is_default=True).exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)


class TelegramSession(models.Model):
    """Per-user, per-bot conversation state for FA/EN flow."""

    class State(models.TextChoices):
        START = 'START', 'Start'
        SELECT_LANGUAGE = 'SELECT_LANGUAGE', 'Select Language'
        ASK_CONTACT = 'ASK_CONTACT', 'Ask Contact'
        CHOOSE_CONTACT_TYPE = 'CHOOSE_CONTACT_TYPE', 'Choose Contact Type'
        ENTER_PHONE = 'ENTER_PHONE', 'Enter Phone'
        ENTER_EMAIL = 'ENTER_EMAIL', 'Enter Email'
        MAIN_MENU = 'MAIN_MENU', 'Main Menu'
        MY_ADS = 'MY_ADS', 'My Ads'
        ENTER_CONTENT = 'ENTER_CONTENT', 'Enter Content'
        SELECT_CATEGORY = 'SELECT_CATEGORY', 'Select Category'
        CONFIRM = 'CONFIRM', 'Confirm'
        SUBMITTED = 'SUBMITTED', 'Submitted'
        EDITING = 'EDITING', 'Editing'
        RESUBMIT_EDIT = 'RESUBMIT_EDIT', 'Resubmit Edit'
        RESUBMIT_CONFIRM = 'RESUBMIT_CONFIRM', 'Resubmit Confirm'

    telegram_user_id = models.BigIntegerField(db_index=True)
    bot = models.ForeignKey(TelegramBot, on_delete=models.CASCADE, related_name='sessions')
    language = models.CharField(max_length=8, blank=True, null=True)  # 'fa' or 'en'
    state = models.CharField(
        max_length=32,
        choices=State.choices,
        default=State.START,
        db_index=True
    )
    context = models.JSONField(default=dict, blank=True)  # draft content, category, etc.
    last_activity = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-last_activity']
        unique_together = [('telegram_user_id', 'bot')]
        indexes = [
            models.Index(fields=['telegram_user_id', 'bot']),
            models.Index(fields=['state']),
        ]
        verbose_name = 'Telegram Session'
        verbose_name_plural = 'Telegram Sessions'

    def __str__(self):
        return f'user={self.telegram_user_id} bot={self.bot_id} state={self.state}'


class TelegramMessageLog(models.Model):
    """Optional: store message history for audit and reliability."""

    bot = models.ForeignKey(TelegramBot, on_delete=models.CASCADE, related_name='message_logs')
    telegram_user_id = models.BigIntegerField(db_index=True)
    direction = models.CharField(max_length=8, choices=[('in', 'In'), ('out', 'Out')])
    text = models.TextField(blank=True)
    raw_payload = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['bot', 'telegram_user_id', 'created_at'])]


class InstagramSettings(models.Model):
    """
    Singleton (pk=1) for Instagram queue behavior.
    - enable_instagram_queue: when ON, approved ads are queued for Instagram instead of posting immediately.
    - last_post_time: timestamp of last successful automated Instagram post (Feed or Story); used for 5 posts/24h rule.
    """
    enable_instagram_queue = models.BooleanField(
        default=False,
        verbose_name='Add post and story on queue',
        help_text='When ON, approved ads are queued for Instagram and published by the scheduler (max 5 per 24h). When OFF, posts go out immediately.',
    )
    last_post_time = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text='When the last successful Instagram post was made by the queue processor (read-only).',
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Instagram Settings'
        verbose_name_plural = 'Instagram Settings'

    @classmethod
    def get_settings(cls):
        """Return the singleton InstagramSettings (pk=1), creating with defaults if needed."""
        try:
            obj, _ = cls.objects.get_or_create(
                pk=1,
                defaults={'enable_instagram_queue': False},
            )
            return obj
        except Exception:
            obj = cls.objects.filter(pk=1).first()
            if obj is None:
                obj = cls(pk=1)
                obj.save()
            return obj


class InstagramConfiguration(models.Model):
    """Instagram (Meta Graph API) credentials. Token encrypted at rest."""

    username = models.CharField(max_length=128, help_text='Instagram account username')
    access_token_encrypted = models.TextField(blank=True)
    page_id = models.CharField(max_length=64, blank=True, help_text='Facebook Page ID linked to Instagram')
    ig_user_id = models.CharField(max_length=64, blank=True, help_text='Instagram Graph API user ID')
    placeholder_image_url = models.URLField(blank=True, help_text='Default image for caption-only ads')
    is_active = models.BooleanField(default=True)
    last_test_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Instagram Configuration'
        verbose_name_plural = 'Instagram Configurations'

    def __str__(self):
        return f'{self.username} (active={self.is_active})'

    def get_decrypted_token(self):
        return decrypt_token(self.access_token_encrypted)

    def set_access_token(self, plain_token: str):
        self.access_token_encrypted = encrypt_token((plain_token or '').strip())


class ApiClient(models.Model):
    """Partner API client. API key stored hashed (one-way)."""

    name = models.CharField(max_length=128, help_text='Client identifier')
    api_key_hashed = models.CharField(max_length=128)  # hashed via Django hashers
    is_active = models.BooleanField(default=True)
    rate_limit_per_min = models.PositiveIntegerField(default=60, help_text='Max requests per minute')
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'API Client'
        verbose_name_plural = 'API Clients'

    def __str__(self):
        return f'{self.name} (active={self.is_active})'


class AdminProfile(models.Model):
    """
    Staff admin profile: extends Django User with Telegram ID for new-request notifications.
    Only users with an AdminProfile are considered "managed admins" for the Admin Management page.
    """
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='admin_profile',
    )
    telegram_id = models.CharField(
        max_length=32,
        blank=True,
        help_text='Telegram chat ID to receive new-request notifications (numeric).',
    )
    is_notified = models.BooleanField(
        default=True,
        help_text='When True, this admin receives Telegram notifications for new requests.',
    )
    admin_nickname = models.CharField(
        max_length=64,
        blank=True,
        help_text='Optional display name for this admin (e.g. for lists).',
    )

    class Meta:
        verbose_name = 'Admin Profile'
        verbose_name_plural = 'Admin Profiles'
        ordering = ['user__username']

    def __str__(self):
        return self.admin_nickname or self.user.username

    def save(self, *args, **kwargs):
        # Store only digits (reject @username or spaces); strip whitespace
        raw = (self.telegram_id or "").strip()
        self.telegram_id = "".join(c for c in raw if c.isdigit())
        super().save(*args, **kwargs)


class ScheduledInstagramPost(models.Model):
    """
    Scheduled Instagram post. Run management command or Celery beat to publish.
    image_url must be publicly accessible. caption can include message, email, phone.
    """

    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PUBLISHED = 'published', 'Published'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    image_url = models.URLField(max_length=2048, help_text='Public URL of image for posting')
    caption = models.TextField(help_text='Caption (message, email, phone). Max 2200 chars.')
    message_text = models.TextField(blank=True, help_text='Original message text')
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    scheduled_at = models.DateTimeField(db_index=True, help_text='When to publish')
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    instagram_media_id = models.CharField(max_length=64, blank=True)
    error_message = models.TextField(blank=True)
    ad = models.ForeignKey(
        AdRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='scheduled_instagram_posts',
    )
    created_at = models.DateTimeField(default=timezone.now)
    published_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['scheduled_at']
        verbose_name = 'Scheduled Instagram Post'
        verbose_name_plural = 'Scheduled Instagram Posts'
        indexes = [
            models.Index(fields=['status', 'scheduled_at']),
        ]

    def __str__(self):
        return f'{self.status} @ {self.scheduled_at}'


class DeliveryLog(models.Model):
    """Per-channel delivery result for an ad (approval notification / posting)."""

    class DeliveryStatus(models.TextChoices):
        PENDING = 'pending', 'Pending'
        QUEUED = 'queued', 'Queued'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    class Channel(models.TextChoices):
        TELEGRAM = 'telegram', 'Telegram'
        TELEGRAM_CHANNEL = 'telegram_channel', 'Telegram Channel'
        INSTAGRAM = 'instagram', 'Instagram'
        INSTAGRAM_STORY = 'instagram_story', 'Instagram Story'
        API = 'api', 'API'
        WEBHOOK = 'webhook', 'External Webhook'

    ad = models.ForeignKey(
        AdRequest,
        on_delete=models.CASCADE,
        related_name='delivery_logs',
    )
    channel = models.CharField(max_length=24, choices=Channel.choices, db_index=True)
    status = models.CharField(
        max_length=16,
        choices=DeliveryStatus.choices,
        default=DeliveryStatus.PENDING,
        db_index=True,
    )
    response_payload = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['ad', 'channel']),
            models.Index(fields=['status']),
        ]
        verbose_name = 'Delivery Log'
        verbose_name_plural = 'Delivery Logs'

    def __str__(self):
        return f'{self.ad_id} {self.channel} {self.status}'


class ActivityLog(models.Model):
    """Audit trail for critical CRM actions."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='activity_logs',
    )
    action = models.CharField(max_length=128)
    object_type = models.CharField(max_length=64, blank=True, default='')
    object_repr = models.CharField(max_length=255, blank=True, default='')
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at']),
            models.Index(fields=['object_type']),
        ]
        verbose_name = 'Activity Log'
        verbose_name_plural = 'Activity Logs'

    def __str__(self):
        return f'{self.action} ({self.object_type})'


def default_adtemplate_coordinates():
    """
    Default coordinates JSON for AdTemplate.
    Primary source for production is static/banner_config.json; these are fallbacks.
    Keys: category, description, phone — each with x, y, size, color, font_path, align; description has max_width; phone has letter_spacing.
    No pseudo-bold (stroke); font weight from .ttf only.
    """
    return {
        'category': {
            'x': 180,
            'y': 288,
            'size': 93,
            'color': '#EEFF00',
            'font_path': '',
            'max_width': 700,
            'align': 'center',
        },
        'description': {
            'x': 215,
            'y': 598,
            'size': 58,
            'color': '#FFFFFF',
            'font_path': '',
            'max_width': 650,
            'align': 'center',
        },
        'phone': {
            'x': 300,
            'y': 1150,
            'size': 48,
            'color': '#131111',
            'font_path': '',
            'max_width': 450,
            'align': 'center',
            'letter_spacing': 2,
        },
    }


# Instagram format constants
FORMAT_POST = 'POST'   # 1080x1350 (4:5)
FORMAT_STORY = 'STORY'  # 1080x1920 (9:16)

FORMAT_DIMENSIONS = {
    FORMAT_POST: (1080, 1350),
    FORMAT_STORY: (1080, 1920),
}

# Story safety zones: top 250px (profile icon) and bottom 250px (send message bar)
STORY_SAFE_TOP = 250
STORY_SAFE_BOTTOM = 250


STORY_Y_OFFSET = 285  # Pixels to shift Y-coordinates when converting post → story


def default_story_coordinates():
    """
    Default story coordinates for 1080x1920 canvas.
    Auto-generated from post defaults by adding STORY_Y_OFFSET to all Y values.
    """
    post = default_adtemplate_coordinates()
    story = {}
    for key, conf in post.items():
        if isinstance(conf, dict):
            story[key] = dict(conf)
            story[key]['y'] = conf.get('y', 0) + STORY_Y_OFFSET
        else:
            story[key] = conf
    return story


class AdTemplate(models.Model):
    """
    Template for automated ad image generation.
    Background image + font + JSON-based coordinates for Category, Description, and Phone.
    """

    name = models.CharField(max_length=128, help_text='Template name')
    background_image = models.ImageField(
        upload_to='ad_templates/backgrounds/',
        help_text='Background image for the ad',
    )
    font_file = models.FileField(
        upload_to='ad_templates/fonts/',
        help_text='Default TrueType font (.ttf) for text overlay',
        blank=True,
        null=True,
    )
    coordinates = models.JSONField(
        default=default_adtemplate_coordinates,
        blank=True,
        help_text=(
            'JSON with keys: category, description, phone. '
            'Each contains x, y, size, color, font_path, align; description also has max_width; phone has letter_spacing. '
            'Primary source is static/banner_config.json.'
        ),
    )
    story_coordinates = models.JSONField(
        default=default_story_coordinates,
        blank=True,
        help_text=(
            'Coordinates for Story format (1080x1920). Same structure as coordinates. '
            'If empty, auto-generated from post coordinates using safety zone logic.'
        ),
    )
    is_active = models.BooleanField(default=True, help_text='Active templates are eligible for auto-selection.')

    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        verbose_name = 'Ad Template'
        # Show as "Template Manager" label in Django admin sidebar
        verbose_name_plural = 'Template Manager'

    def __str__(self):
        return self.name


# Common rejection reasons for quick-select in UI (list and detail)
REJECTION_REASONS = [
    ('Spam', 'Spam'),
    ('Invalid or missing phone number', 'Invalid or missing phone number'),
    ('Duplicate ad', 'Duplicate ad'),
    ('Inappropriate content', 'Inappropriate content'),
    ('Low quality or incomplete', 'Low quality or incomplete'),
    ('Violates community guidelines', 'Violates community guidelines'),
    ('Other', 'Other'),
]

# Predefined rejection reasons for Request Detail page (dropdown); expandable.
REJECTION_REASONS_DETAIL = [
    ('spam', 'Spam / Advertising not allowed'),
    ('offensive', 'Offensive content'),
    ('incomplete', 'Incomplete content / missing info'),
    ('duplicate', 'Duplicate request'),
    ('other', 'Other (manual comment optional)'),
]
