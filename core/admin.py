"""
Iraniu — Django admin registration.
Privacy: mask email/phone in list; full data only in detail. Never log contact info.
"""

from django import forms
from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html
from datetime import timedelta
from .models import (
    SiteConfiguration,
    SystemStatus,
    Notification,
    AdTemplate,
    Category,
    AdRequest,
    AdminProfile,
    TelegramBot,
    TelegramChannel,
    TelegramSession,
    TelegramMessageLog,
    TelegramUser,
    VerificationCode,
    InstagramConfiguration,
    ApiClient,
    DeliveryLog,
    ScheduledInstagramPost,
    ActivityLog,
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


class TelegramChannelInline(admin.TabularInline):
    model = TelegramChannel
    fk_name = 'site_config'
    extra = 0
    fields = ['title', 'channel_id', 'bot_connection', 'is_default', 'is_active']
    raw_id_fields = ['bot_connection']
    ordering = ['title']


@admin.register(SiteConfiguration)
class SiteConfigurationAdmin(admin.ModelAdmin):
    list_display = ['pk', 'is_ai_enabled', 'is_channel_active', 'use_webhook', 'updated_at']
    fieldsets = (
        ('AI', {
            'fields': ('is_ai_enabled', 'openai_api_key', 'openai_model', 'ai_system_prompt'),
        }),
        ('Telegram (legacy / webhook)', {
            'fields': ('telegram_bot_token', 'telegram_bot_username', 'telegram_webhook_url', 'use_webhook', 'production_base_url'),
        }),
        ('Default Telegram channel (ads)', {
            'fields': ('telegram_channel_id', 'telegram_channel_title', 'is_channel_active', 'default_telegram_bot'),
            'description': 'Approved ads are posted here when "Channel active" is checked. Bot must have admin rights in the channel.',
        }),
        ('Messaging', {
            'fields': ('approval_message_template', 'rejection_message_template', 'submission_ack_message'),
        }),
        ('Instagram', {
            'fields': ('is_instagram_enabled', 'instagram_app_id', 'instagram_business_id', 'facebook_access_token_encrypted'),
            'description': 'is_instagram_enabled is auto-managed: it becomes True when all required Instagram fields are filled.',
        }),
    )
    filter_horizontal = ()
    raw_id_fields = ['default_telegram_bot']
    inlines = [TelegramChannelInline]


@admin.register(SystemStatus)
class SystemStatusAdmin(admin.ModelAdmin):
    list_display = ['pk', 'last_heartbeat', 'is_bot_active', 'updated_at']
    readonly_fields = ['last_heartbeat', 'is_bot_active', 'active_errors', 'updated_at']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['id', 'level', 'message_short', 'is_read', 'created_at']
    list_filter = ['level', 'is_read']
    search_fields = ['message']
    readonly_fields = ['created_at']

    def message_short(self, obj):
        return (obj.message or '')[:60] + ('…' if len(obj.message or '') > 60 else '')
    message_short.short_description = 'Message'


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
    list_display = ['name', 'name_fa', 'slug', 'color', 'is_active', 'order']
    list_filter = ['is_active']
    search_fields = ['name', 'name_fa', 'slug']
    list_editable = ['is_active', 'order']


@admin.register(AdRequest)
class AdRequestAdmin(admin.ModelAdmin):
    list_display = ['uuid', 'category', 'status', 'bot', 'user', 'created_at']
    list_filter = ['status', 'category']
    search_fields = ['content', 'uuid']
    readonly_fields = ['uuid', 'created_at', 'updated_at', 'raw_telegram_json']


class TelegramBotAdminForm(forms.ModelForm):
    """Allow editing the bot token via a 'New token' field; stored encrypted in bot_token_encrypted."""
    new_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(attrs={"placeholder": "Leave blank to keep current token", "autocomplete": "new-password"}),
        label="New token",
        help_text="Set a new Telegram bot token. Leave blank to keep the current token (required when creating a new bot).",
    )

    class Meta:
        model = TelegramBot
        exclude = ["bot_token_encrypted"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            self.fields["new_token"].required = True
            self.fields["new_token"].help_text = "Telegram bot token from @BotFather."

    def save(self, commit=True):
        obj = super().save(commit=commit)
        if commit and self.cleaned_data.get("new_token"):
            obj.set_token(self.cleaned_data["new_token"])
            obj.save(update_fields=["bot_token_encrypted"])
        return obj


@admin.register(TelegramBot)
class TelegramBotAdmin(admin.ModelAdmin):
    form = TelegramBotAdminForm
    list_display = [
        'name', 'username', 'environment', 'is_default', 'is_active', 'status', 'mode', 'worker_pid',
        'last_heartbeat', 'last_webhook_received', 'last_error_short', 'updated_at',
    ]
    list_filter = ['environment', 'is_active', 'is_default', 'status', 'mode']
    search_fields = ['name', 'username']
    readonly_fields = [
        'created_at', 'updated_at', 'last_heartbeat', 'last_webhook_received',
        'worker_pid', 'worker_started_at', 'last_error', 'status', 'webhook_secret_token',
    ]
    exclude = ['bot_token_encrypted']
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

@admin.register(TelegramChannel)
class TelegramChannelAdmin(admin.ModelAdmin):
    list_display = ['title', 'channel_id', 'bot_environment', 'is_default', 'is_active', 'bot_connection', 'updated_at']
    list_filter = ['is_active', 'is_default', 'bot_connection__environment']

    def bot_environment(self, obj):
        return getattr(obj.bot_connection, "environment", "—") if obj.bot_connection else "—"
    bot_environment.short_description = "Environment"
    search_fields = ['title', 'channel_id']
    list_editable = ['is_active']
    raw_id_fields = ['bot_connection']
    readonly_fields = ['created_at', 'updated_at']
    actions = ['set_as_default_action']

    @admin.action(description='Set as Default')
    def set_as_default_action(self, request, queryset):
        chosen = queryset.first()
        if not chosen:
            self.message_user(request, 'No channel selected.', level=admin.constants.WARNING)
            return
        TelegramChannel.objects.filter(is_default=True).exclude(pk=chosen.pk).update(is_default=False)
        chosen.is_default = True
        chosen.save(update_fields=['is_default'])
        self.message_user(request, f'"{chosen.title}" is now the default channel.', level=admin.constants.SUCCESS)


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


@admin.register(ActivityLog)
class ActivityLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'user', 'action', 'object_type', 'object_repr']
    list_filter = ['object_type', 'action']
    search_fields = ['action', 'object_type', 'object_repr', 'user__username']
    readonly_fields = ['created_at', 'user', 'action', 'object_type', 'object_repr', 'metadata']

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AdminProfile)
class AdminProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'admin_nickname', 'telegram_id', 'is_notified']
    list_filter = ['is_notified']
    search_fields = ['user__username', 'admin_nickname', 'telegram_id']
    raw_id_fields = ['user']


@admin.register(AdTemplate)
class AdTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_active', 'created_at', 'updated_at']
    list_filter = ['is_active']
    search_fields = ['name']
    readonly_fields = ['created_at', 'updated_at']
    actions = ['open_coordinate_lab_action']

    def get_urls(self):
        from django.urls import path

        urls = super().get_urls()
        extra = [
            path(
                '<int:object_id>/template-coordinate-lab/',
                self.admin_site.admin_view(self.template_coordinate_lab_view),
                name='core_adtemplate_coordinate_lab',
            ),
        ]
        return extra + urls

    def template_coordinate_lab_view(self, request, object_id):
        """Redirect to the new Manual Coordinate Editor (drag-and-drop removed)."""
        from django.shortcuts import get_object_or_404, redirect
        from django.urls import reverse

        tpl = get_object_or_404(AdTemplate, pk=object_id)
        return redirect(reverse('template_manual_edit', args=[tpl.pk]))

    @admin.action(description='Open Coordinate Editor')
    def open_coordinate_lab_action(self, request, queryset):
        from django.shortcuts import redirect
        from django.urls import reverse
        first = queryset.first()
        if not first:
            self.message_user(request, 'Select at least one template.', level=admin.constants.WARNING)
            return
        return redirect(reverse('template_manual_edit', args=[first.pk]))
