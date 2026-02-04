"""
Iranio — Bilingual (FA/EN) message registry.
Never hardcode text in handlers; always use get_message(key, lang).
"""

MESSAGES = {
    "start": {
        "en": "Welcome to Iranio. Please choose your language.",
        "fa": "به ایرانيو خوش آمدید. لطفاً زبان خود را انتخاب کنید.",
    },
    "select_language": {
        "en": "Choose your language",
        "fa": "زبان خود را انتخاب کنید",
    },
    "lang_en": {
        "en": "English",
        "fa": "English",
    },
    "lang_fa": {
        "en": "فارسی",
        "fa": "فارسی",
    },
    "main_menu": {
        "en": "Main menu",
        "fa": "منوی اصلی",
    },
    "create_new_ad": {
        "en": "Create new ad",
        "fa": "ثبت آگهی جدید",
    },
    "enter_ad_text": {
        "en": "Enter your ad text",
        "fa": "متن آگهی را وارد کنید",
    },
    "choose_category": {
        "en": "Choose category",
        "fa": "دسته‌بندی را انتخاب کنید",
    },
    "confirm_submission": {
        "en": "Confirm submission?",
        "fa": "تأیید می‌کنید؟",
    },
    "submitted": {
        "en": "Your ad has been submitted. We will notify you when it is reviewed.",
        "fa": "آگهی شما ثبت شد. پس از بررسی به شما اطلاع می‌دهیم.",
    },
    "cancel": {
        "en": "Cancel",
        "fa": "انصراف",
    },
    "back": {
        "en": "Back",
        "fa": "بازگشت",
    },
    # Contact flow
    "add_contact_ask": {
        "en": "Do you want to add contact info? (optional)",
        "fa": "آیا می‌خواهید اطلاعات تماس اضافه کنید؟ (اختیاری)",
    },
    "add_contact_yes": {"en": "Yes, add contact", "fa": "بله، اضافه کن"},
    "add_contact_skip": {"en": "Skip", "fa": "رد کردن"},
    "choose_contact_type": {
        "en": "Choose: phone or email",
        "fa": "انتخاب کنید: تلفن یا ایمیل",
    },
    "contact_phone": {"en": "Phone", "fa": "تلفن"},
    "contact_email": {"en": "Email", "fa": "ایمیل"},
    "enter_phone": {
        "en": "Enter your phone number (E.164, e.g. +989123456789)",
        "fa": "شماره تلفن را وارد کنید (مثال: ۹۸۹۱۲۳۴۵۶۷۸۹+)",
    },
    "enter_email": {
        "en": "Enter your email address",
        "fa": "آدرس ایمیل را وارد کنید",
    },
    "invalid_phone": {"en": "Invalid phone format. Use E.164 (max 15 digits).", "fa": "فرمت تلفن نامعتبر است."},
    "invalid_email": {"en": "Invalid email address.", "fa": "آدرس ایمیل نامعتبر است."},
    "contact_saved": {"en": "Contact info saved.", "fa": "اطلاعات تماس ذخیره شد."},
    # Categories (for keyboard)
    "category_job": {"en": "Job", "fa": "شغل"},
    "category_rent": {"en": "Rent", "fa": "اجاره"},
    "category_events": {"en": "Events", "fa": "رویدادها"},
    "category_services": {"en": "Services", "fa": "خدمات"},
    "category_sale": {"en": "Sale", "fa": "فروش"},
    "category_other": {"en": "Other", "fa": "سایر"},
}


def get_message(key: str, lang: str | None) -> str:
    """
    Return message for key in language. lang in ('en', 'fa') or None.
    Falls back to 'en' if key or lang missing.
    """
    if not key or key not in MESSAGES:
        return key or ""
    msgs = MESSAGES[key]
    if lang and lang in msgs:
        return msgs[lang]
    return msgs.get("en", list(msgs.values())[0] if msgs else "")
