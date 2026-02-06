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
    AdRequest,
    TelegramBot,
    TelegramSession,
    TelegramMessageLog,
    TelegramUser,
    VerificationCode,
    InstagramConfiguration,
    ApiClient,
    DeliveryLog,
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


@admin.register(AdRequest)
class AdRequestAdmin(admin.ModelAdmin):
    list_display = ['uuid', 'category', 'status', 'bot', 'user', 'created_at']
    list_filter = ['status', 'category']
    search_fields = ['content', 'uuid']
    readonly_fields = ['uuid', 'created_at', 'updated_at', 'raw_telegram_json']


@admin.register(TelegramBot)
class TelegramBotAdmin(admin.ModelAdmin):
    list_display = [
        'name', 'username', 'is_active', 'status', 'worker_pid',
        'last_heartbeat', 'last_error_short', 'updated_at',
    ]
    list_filter = ['is_active', 'status', 'mode']
    search_fields = ['name', 'username']
    readonly_fields = [
        'created_at', 'updated_at', 'last_heartbeat', 'worker_pid',
        'worker_started_at', 'last_error', 'status',
    ]
    exclude = ['bot_token_encrypted']  # Never show in admin; use set_token in code only

    def last_error_short(self, obj):
        if not obj.last_error:
            return "—"
        s = obj.last_error[:80] + "…" if len(obj.last_error) > 80 else obj.last_error
        return s
    last_error_short.short_description = "Last Error"


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


@admin.register(DeliveryLog)
class DeliveryLogAdmin(admin.ModelAdmin):
    list_display = ['ad', 'channel', 'status', 'created_at']
    list_filter = ['channel', 'status']
    search_fields = ['ad__uuid', 'error_message']
    readonly_fields = ['created_at']
