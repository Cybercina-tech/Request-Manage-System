"""
Iraniu — Django admin registration.
Privacy: mask email/phone in list; full data only in detail. Never log contact info.
"""

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from datetime import timedelta
from .models import (
    SiteConfiguration,
    Category,
    AdRequest,
    AdminProfile,
    TelegramBot,
    TelegramSession,
    TelegramMessageLog,
    TelegramUser,
    VerificationCode,
    InstagramConfiguration,
    ApiClient,
    DeliveryLog,
    ScheduledInstagramPost,
)


def mask_contact(value, visible=2):
    """Mask for list display; do not log."""
    if not value or len(value) < 4:
        return "••••"
    return value[:2] + "••••" + value[-visible:] if len(value) > 4 else "••••"


class ActiveUserFilter(admin.SimpleListFilter):
    title = "active"
    parameter_name = "active"

    def lookups(self, request, model_admin):
        return (("yes", "Active (last 30 days)"), ("no", "Inactive"))

    def queryset(self, request, queryset):
        if self.value() == "yes":
            threshold = timezone.now() - timedelta(days=30)
            return queryset.filter(last_seen__gte=threshold)
        if self.value() == "no":
            threshold = timezone.now() - timedelta(days=30)
            return queryset.filter(last_seen__lt=threshold)
        return queryset


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    list_display = ['pk', 'is_ai_enabled', 'use_webhook', 'updated_at']


class AdRequestInline(admin.TabularInline):
    model = AdRequest
    fk_name = 'user'
    extra = 0
    show_change_link = True
    fields = ['uuid', 'category', 'status', 'created_at']
    readonly_fields = ['uuid', 'category', 'status', 'created_at']
    max_num = 20

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = [
        'telegram_user_id', 'username', 'first_name', 'masked_phone', 'masked_email',
        'phone_verified', 'email_verified', 'last_seen', 'created_at',
    ]
    list_filter = ['phone_verified', 'email_verified', 'is_bot', ActiveUserFilter]
    search_fields = ['telegram_user_id', 'username', 'first_name', 'last_name']
    readonly_fields = ['created_at', 'updated_at', 'last_seen']
    inlines = [AdRequestInline]
    date_hierarchy = 'last_seen'

    def masked_phone(self, obj):
        if not obj.phone_number:
            return "—"
        return mask_contact(obj.phone_number, 2)
    masked_phone.short_description = "Phone"

    def masked_email(self, obj):
        if not obj.email:
            return "—"
        return mask_contact(obj.email, 2)
    masked_email.short_description = "Email"

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related('ad_requests')


@admin.register(VerificationCode)
class VerificationCodeAdmin(admin.ModelAdmin):
    list_display = ['id', 'user', 'channel', 'expires_at', 'used', 'created_at']
    list_filter = ['channel', 'used']
    search_fields = ['user__telegram_user_id']
    readonly_fields = ['user', 'channel', 'code_hashed', 'expires_at', 'used', 'created_at']
    # No add/change: codes are created only via otp.generate_code

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'slug', 'color', 'is_active', 'order']
    list_filter = ['is_active']
    search_fields = ['name', 'slug']
    list_editable = ['is_active', 'order']


@admin.register(AdRequest)
class AdRequestAdmin(admin.ModelAdmin):
    list_display = ['uuid', 'category', 'status', 'bot', 'user', 'created_at']
    list_filter = ['status', 'category']
    search_fields = ['content', 'uuid']
    readonly_fields = ['uuid', 'created_at', 'updated_at', 'raw_telegram_json']


@admin.register(TelegramBot)
class TelegramBotAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'username', 'is_default', 'is_active', 'status', 'mode', 'worker_pid',
        'last_heartbeat', 'last_webhook_received', 'last_error_short', 'updated_at',
    ]
    list_filter = ['is_active', 'is_default', 'status', 'mode']
    search_fields = ['name', 'username']
    readonly_fields = [
        'created_at', 'updated_at', 'last_heartbeat', 'last_webhook_received',
        'worker_pid', 'worker_started_at', 'last_error', 'status', 'webhook_secret_token',
    ]
    exclude = ['bot_token_encrypted']  # Never show in admin; use set_token in code only
    actions = ['activate_webhook_mode', 'delete_webhook_action', 'check_webhook_status_action']

    def last_error_short(self, obj):
        if not obj.last_error:
            return "—"
        s = obj.last_error[:80] + "…" if len(obj.last_error) > 80 else obj.last_error
        return s
    last_error_short.short_description = "Last Error"

    @admin.action(description='Switch to Webhook Mode')
    def activate_webhook_mode(self, request, queryset):
        from core.services.bot_manager import activate_webhook
        done = 0
        urls = []
        for bot in queryset:
            success, msg, full_url = activate_webhook(bot)
            if success:
                done += 1
                if full_url:
                    urls.append(f"{bot.name}: {full_url}")
            else:
                self.message_user(request, f"{bot.name}: {msg}", level=admin.constants.WARNING)
        if done:
            self.message_user(
                request,
                f"Webhook activated for {done} bot(s). URL(s): " + "; ".join(urls) if urls else f"Webhook activated for {done} bot(s).",
                level=admin.constants.SUCCESS,
            )
        elif not urls:
            self.message_user(request, "No bot activated. Set production_base_url (HTTPS) in Site Configuration.", level=admin.constants.ERROR)

    @admin.action(description='Delete Webhook')
    def delete_webhook_action(self, request, queryset):
        from core.services.telegram_client import delete_webhook
        done = 0
        errors = []
        for bot in queryset:
            token = bot.get_decrypted_token()
            if not token:
                errors.append(f"{bot.name}: No token")
                continue
            ok, err = delete_webhook(token, drop_pending_updates=True)
            if ok:
                bot.webhook_url = ""
                bot.save(update_fields=["webhook_url"])
                done += 1
            else:
                errors.append(f"{bot.name}: {err or 'Failed'}")
        if done:
            self.message_user(request, f"Webhook deleted for {done} bot(s).", level=admin.constants.SUCCESS)
        for msg in errors:
            self.message_user(request, msg, level=admin.constants.ERROR)

    @admin.action(description='Check Webhook Status')
    def check_webhook_status_action(self, request, queryset):
        from core.services.telegram_client import get_webhook_info
        lines = []
        for bot in queryset:
            token = bot.get_decrypted_token()
            if not token:
                lines.append(f"{bot.name}: No token")
                continue
            success, info, err = get_webhook_info(token)
            if not success:
                lines.append(f"{bot.name}: {err or 'getWebhookInfo failed'}")
                continue
            url = (info or {}).get("url") or "(not set)"
            pending = (info or {}).get("pending_update_count", 0)
            lines.append(f"{bot.name}: url={url}, pending_updates={pending}")
        if lines:
            self.message_user(request, " | ".join(lines), level=admin.constants.SUCCESS)


@admin.register(TelegramSession)
class TelegramSessionAdmin(admin.ModelAdmin):
    list_display = ['telegram_user_id', 'bot', 'language', 'state', 'last_activity']
    list_filter = ['state', 'bot']
    search_fields = ['telegram_user_id']
    readonly_fields = ['created_at', 'updated_at']


@admin.register(TelegramMessageLog)
class TelegramMessageLogAdmin(admin.ModelAdmin):
    list_display = ['bot', 'telegram_user_id', 'direction', 'created_at']
    list_filter = ['direction', 'bot']
    search_fields = ['text', 'telegram_user_id']
    readonly_fields = ['created_at']


@admin.register(InstagramConfiguration)
class InstagramConfigurationAdmin(admin.ModelAdmin):
    list_display = ['username', 'page_id', 'is_active', 'last_test_at', 'updated_at']
    list_filter = ['is_active']
    search_fields = ['username']
    readonly_fields = ['created_at', 'updated_at', 'last_test_at']
    exclude = ['access_token_encrypted']


@admin.register(ApiClient)
class ApiClientAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'rate_limit_per_min', 'last_used_at', 'created_at']
    list_filter = ['is_active']
    search_fields = ['name']
    readonly_fields = ['created_at', 'last_used_at']
    exclude = ['api_key_hashed']


@admin.register(ScheduledInstagramPost)
class ScheduledInstagramPostAdmin(admin.ModelAdmin):
    list_display = ['pk', 'status', 'scheduled_at', 'published_at', 'ad', 'created_at']
    list_filter = ['status']
    search_fields = ['message_text', 'caption', 'email', 'phone']
    readonly_fields = ['instagram_media_id', 'error_message', 'published_at', 'created_at']
    date_hierarchy = 'scheduled_at'
    list_editable = ['status']


@admin.register(DeliveryLog)
class DeliveryLogAdmin(admin.ModelAdmin):
    list_display = ['ad', 'channel', 'status', 'created_at']
    list_filter = ['channel', 'status']
    search_fields = ['ad__uuid', 'error_message']
    readonly_fields = ['created_at']


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'admin_nickname', 'telegram_id', 'is_notified']
    list_filter = ['is_notified']
    search_fields = ['user__username', 'admin_nickname', 'telegram_id']
    raw_id_fields = ['user']
