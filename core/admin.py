"""
Iranio — Django admin registration.
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
    list_display = ['name', 'username', 'is_active', 'status', 'last_heartbeat', 'updated_at']
    list_filter = ['is_active', 'status']
    search_fields = ['name', 'username']
    readonly_fields = ['created_at', 'updated_at', 'last_heartbeat']
    exclude = ['bot_token_encrypted']  # Never show in admin; use set_token in code only


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
