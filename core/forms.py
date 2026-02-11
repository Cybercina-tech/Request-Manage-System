"""
Iraniu — Forms for staff views.
"""

from django import forms

from core.models import AdTemplate, TelegramBot, TelegramChannel


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
