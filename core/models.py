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
    # Instagram Business (for Post to Feed/Story; fallback if no InstagramConfiguration)
    instagram_business_id = models.CharField(
        max_length=64,
        blank=True,
        help_text='Instagram Graph API user ID (Business account linked to Facebook Page).',
    )
    facebook_access_token_encrypted = models.TextField(
        blank=True,
        help_text='Long-lived Facebook/Instagram access token (encrypted at rest).',
    )
    # Maintenance
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Site Configuration'
        verbose_name_plural = 'Site Configuration'

    def clean(self):
        from django.core.exceptions import ValidationError
        url = (self.production_base_url or "").strip()
        if url and not url.startswith("https://"):
            raise ValidationError({
                "production_base_url": "Must be HTTPS (e.g. https://iraniu.ir). Required for Telegram webhook.",
            })

    def save(self, *args, **kwargs):
        if not self.pk and SiteConfiguration.objects.exists():
            return SiteConfiguration.objects.first()
        result = super().save(*args, **kwargs)
        try:
            from django.core.cache import cache
            cache.delete('site_config_singleton')
        except Exception:
            pass
        return result

    @classmethod
    def get_config(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def get_facebook_access_token(self) -> str:
        """Return decrypted Facebook access token for Instagram API."""
        return decrypt_token(self.facebook_access_token_encrypted)

    def set_facebook_access_token(self, plain_token: str):
        self.facebook_access_token_encrypted = encrypt_token((plain_token or '').strip())


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
        return self.name

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
    content = models.TextField()
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

    def get_category_color(self):
        """Hex color for badge styling."""
        return (self.category.color or '#7C4DFF') if self.category else '#7C4DFF'


class TelegramBot(models.Model):
    """
    Multi-bot support: one project can manage multiple bots.
    Fields: mode (Webhook/Polling), bot_token_encrypted (token at rest), is_active.
    Only active bots send/receive. Token is validated via getMe on update; use Reset Bot Connection to clear webhook and re-sync.
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

    name = models.CharField(max_length=128, help_text='Human-readable name')
    bot_token_encrypted = models.TextField(blank=True)  # Encrypted at rest; never expose in templates
    username = models.CharField(max_length=64, blank=True, help_text='Bot username without @')
    is_active = models.BooleanField(default=True)
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
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'

    class Channel(models.TextChoices):
        TELEGRAM = 'telegram', 'Telegram'
        INSTAGRAM = 'instagram', 'Instagram'
        API = 'api', 'API'

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
