"""
Iraniu — Forms for staff views.
"""

from django import forms
from django.core.validators import FileExtensionValidator

from core.models import AdTemplate, SiteConfiguration, TelegramBot, TelegramChannel
from core.utils.validation import validate_uploaded_image, parse_hex_color

MAX_TEMPLATE_BG_SIZE_BYTES = 8 * 1024 * 1024
MAX_TEST_BG_SIZE_BYTES = 8 * 1024 * 1024
ALLOWED_FONT_EXTENSIONS = ["ttf", "otf"]


class AdTemplateCreateForm(forms.ModelForm):
    """Form to create a new AdTemplate (name, background_image, font_file). Redirects to Coordinate Lab after save."""

    class Meta:
        model = AdTemplate
        fields = ('name', 'background_image', 'font_file')
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-control',
                'placeholder': 'e.g. Standard 1080x1080',
                'maxlength': 128,
            }),
            'background_image': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': 'image/*',
            }),
            'font_file': forms.FileInput(attrs={
                'class': 'form-control',
                'accept': '.ttf,.otf',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['font_file'].required = False
        self.fields["font_file"].validators.append(FileExtensionValidator(allowed_extensions=ALLOWED_FONT_EXTENSIONS))

    def clean_background_image(self):
        """Validate template background type/size/content."""
        image = self.cleaned_data.get("background_image")
        validate_uploaded_image(image, max_size_bytes=MAX_TEMPLATE_BG_SIZE_BYTES, field_name="Background image")
        return image

    def clean_font_file(self):
        """Validate optional font file extension and size."""
        font_file = self.cleaned_data.get("font_file")
        if not font_file:
            return font_file
        if font_file.size > 2 * 1024 * 1024:
            raise forms.ValidationError("Font file size must be <= 2.0MB.")
        return font_file


class TemplateTesterForm(forms.Form):
    """Form for Ad Template Tester: template, dummy text, and optional custom background."""

    template_id = forms.IntegerField(
        required=True,
        min_value=1,
        label="Template",
        widget=forms.Select(attrs={"class": "form-select form-control"}),
    )
    category_text = forms.CharField(
        required=True,
        initial="Category Heading",
        max_length=200,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Real Estate"}),
    )
    ad_text = forms.CharField(
        required=True,
        initial="Sample ad text for preview. Change this in the form.",
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 4}),
    )
    phone_number = forms.CharField(
        required=False,
        initial="+98 912 345 6789",
        max_length=32,
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    test_background = forms.ImageField(
        required=False,
        label="Upload temporary background",
        help_text="Optional. Override the template's background for this preview only.",
        widget=forms.FileInput(attrs={"class": "form-control", "accept": "image/*"}),
    )

    def __init__(self, *args, **kwargs):
        templates = kwargs.pop("templates", None)
        super().__init__(*args, **kwargs)
        if templates is not None:
            choices = [("", "— Select —")] + [(t.pk, t.name) for t in templates]
            self.fields["template_id"].widget = forms.Select(
                attrs={"class": "form-select form-control"},
                choices=choices,
            )

    def clean_test_background(self):
        """Validate optional temporary tester background upload."""
        image = self.cleaned_data.get("test_background")
        validate_uploaded_image(image, max_size_bytes=MAX_TEST_BG_SIZE_BYTES, field_name="Temporary background")
        return image


class ChannelForm(forms.ModelForm):
    """Add/Edit Telegram Channel: title, channel_id, bot_connection."""

    class Meta:
        model = TelegramChannel
        fields = ("title", "channel_id", "bot_connection")
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Live Ads Channel"}),
            "channel_id": forms.TextInput(attrs={"class": "form-control", "placeholder": "-1001234567890"}),
            "bot_connection": forms.Select(attrs={"class": "form-select form-control"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.conf import settings
        env = getattr(settings, "ENVIRONMENT", "PROD")
        self.fields["bot_connection"].queryset = TelegramBot.objects.filter(
            environment=env, is_active=True
        ).order_by("-is_default", "name")
        self.fields["bot_connection"].empty_label = "— Select bot —"


# ---------- Settings Hub card forms ----------


class InstagramBusinessForm(forms.Form):
    """Instagram Business API card: App ID, App Secret, Business ID, Long-lived token."""

    app_id = forms.CharField(
        max_length=64,
        required=False,
        label="App ID",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Facebook App ID"}),
    )
    app_secret = forms.CharField(
        max_length=255,
        required=False,
        label="App Secret",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "••••••••", "autocomplete": "new-password"}),
    )
    instagram_business_id = forms.CharField(
        max_length=64,
        required=False,
        label="Instagram Business ID",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "Instagram Graph API user ID"}),
    )
    long_lived_access_token = forms.CharField(
        max_length=512,
        required=False,
        label="Long-lived Access Token",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "••••••••", "autocomplete": "new-password"}),
    )


class TelegramBotConfigForm(forms.Form):
    """Telegram Bot Configuration card: token, username, webhook URL (for default bot)."""

    bot_token = forms.CharField(
        max_length=255,
        required=False,
        label="Bot Token",
        widget=forms.PasswordInput(attrs={"class": "form-control", "placeholder": "••••••••", "autocomplete": "new-password"}),
    )
    bot_username = forms.CharField(
        max_length=64,
        required=False,
        label="Bot Username",
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "username without @"}),
    )
    webhook_url = forms.URLField(
        max_length=512,
        required=False,
        label="Webhook URL",
        widget=forms.URLInput(attrs={"class": "form-control", "placeholder": "https://..."}),
    )


class DesignDefaultsForm(forms.ModelForm):
    """Global Design Defaults card: font, colors, watermark, opacity."""

    class Meta:
        model = SiteConfiguration
        fields = (
            "default_font",
            "default_primary_color",
            "default_secondary_color",
            "default_accent_color",
            "default_watermark",
            "default_watermark_opacity",
        )
        widgets = {
            "default_font": forms.Select(attrs={"class": "form-select form-control"}),
            "default_primary_color": forms.TextInput(
                attrs={"class": "form-control form-control-color", "type": "color", "style": "height: 2.5rem; min-width: 3rem;"}
            ),
            "default_secondary_color": forms.TextInput(
                attrs={"class": "form-control form-control-color", "type": "color", "style": "height: 2.5rem; min-width: 3rem;"}
            ),
            "default_accent_color": forms.TextInput(
                attrs={"class": "form-control form-control-color", "type": "color", "style": "height: 2.5rem; min-width: 3rem;"}
            ),
            "default_watermark": forms.FileInput(attrs={"class": "form-control", "accept": "image/*"}),
            "default_watermark_opacity": forms.NumberInput(
                attrs={"class": "form-control", "min": 0, "max": 100, "type": "range", "step": 1}
            ),
        }

    def __init__(self, font_choices=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if font_choices is not None:
            self.fields["default_font"].widget.choices = [("", "— Select —")] + list(font_choices)
        self.fields["default_watermark"].required = False

    def clean_default_primary_color(self):
        from django.core.exceptions import ValidationError
        try:
            return parse_hex_color(
                self.cleaned_data.get("default_primary_color"),
                field_name="Primary color",
                default="#2b8adf",
            )
        except ValidationError:
            return "#2b8adf"

    def clean_default_secondary_color(self):
        from django.core.exceptions import ValidationError
        try:
            return parse_hex_color(
                self.cleaned_data.get("default_secondary_color"),
                field_name="Secondary color",
                default="#3fb98f",
            )
        except ValidationError:
            return "#3fb98f"

    def clean_default_accent_color(self):
        from django.core.exceptions import ValidationError
        try:
            return parse_hex_color(
                self.cleaned_data.get("default_accent_color"),
                field_name="Accent color",
                default="#39a0f1",
            )
        except ValidationError:
            return "#39a0f1"

    def clean_default_watermark(self):
        image = self.cleaned_data.get("default_watermark")
        if image:
            validate_uploaded_image(image, max_size_bytes=2 * 1024 * 1024, field_name="Watermark image")
        return image
