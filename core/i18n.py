"""
Iraniu — Bilingual (FA/EN) message registry.
Never hardcode text in handlers; always use get_message(key, lang).
Emojis used for friendly UX.
"""

MESSAGES = {
    # ——— Start & language ———
    "start": {
        "en": "👋 Hello! Welcome to Iraniu. Please choose your language.",
        "fa": "👋 سلام! به ایرانيو خوش آمدید. لطفاً زبان خود را انتخاب کنید.",
    },
    "select_language": {
        "en": "🌐 Choose your language",
        "fa": "🌐 زبان خود را انتخاب کنید",
    },
    "lang_en": {
        "en": "🇬🇧 English",
        "fa": "🇬🇧 English",
    },
    "lang_fa": {
        "en": "🇮🇷 فارسی",
        "fa": "🇮🇷 فارسی",
    },
    # ——— Main menu ———
    "main_menu": {
        "en": "📋 Main menu",
        "fa": "📋 منوی اصلی",
    },
    "create_new_ad": {
        "en": "✨ Create new ad",
        "fa": "✨ ثبت آگهی جدید",
    },
    # ——— Ad content flow ———
    "enter_ad_text": {
        "en": "✍️ Enter your ad text (you can send one message with your full ad).",
        "fa": "✍️ متن آگهی را وارد کنید (یک پیام با متن کامل آگهی بفرستید).",
    },
    "choose_category": {
        "en": "📂 Choose category",
        "fa": "📂 دسته‌بندی را انتخاب کنید",
    },
    "content_confirm": {
        "en": "📝 Your ad:",
        "fa": "📝 آگهی شما:",
    },
    "category_confirm": {
        "en": "📂 Category:",
        "fa": "📂 دسته:",
    },
    "confirm_submission": {
        "en": "✅ Is this correct? Confirm to submit.",
        "fa": "✅ درست است؟ تأیید کنید تا ارسال شود.",
    },
    "submitted": {
        "en": "🎉 Your ad has been submitted! We will notify you when it is reviewed.",
        "fa": "🎉 آگهی شما ثبت شد! پس از بررسی به شما اطلاع می‌دهیم.",
    },
    "thank_you_emoji": {
        "en": "🙏 Thank you for using Iraniu!",
        "fa": "🙏 از اینکه از ایرانيو استفاده می‌کنید متشکریم!",
    },
    "cancel": {
        "en": "❌ Cancel",
        "fa": "❌ انصراف",
    },
    "back": {
        "en": "◀️ Back",
        "fa": "◀️ بازگشت",
    },
    "edit_btn": {
        "en": "✏️ Edit",
        "fa": "✏️ ویرایش",
    },
    "confirm_yes_btn": {
        "en": "✅ Yes, confirm",
        "fa": "✅ بله، تأیید",
    },
    # ——— Contact at end of flow ———
    "ask_contact": {
        "en": "📱 Share your phone number so we can reach you (optional). Tap the button below or skip.",
        "fa": "📱 برای تماس با شما شماره تلفن به اشتراک بگذارید (اختیاری). دکمه زیر را بزنید یا رد کنید.",
    },
    "share_contact_btn": {
        "en": "📲 Share my phone number",
        "fa": "📲 اشتراک‌گذاری شماره من",
    },
    "contact_skip": {
        "en": "⏭️ Skip",
        "fa": "⏭️ رد کردن",
    },
    "ask_email": {
        "en": "📧 Enter your email (optional), or skip.",
        "fa": "📧 ایمیل خود را وارد کنید (اختیاری)، یا رد کنید.",
    },
    "email_skip": {
        "en": "⏭️ Skip email",
        "fa": "⏭️ رد کردن ایمیل",
    },
    # Legacy / alternate contact keys (kept for compatibility)
    "add_contact_ask": {
        "en": "📱 Do you want to add contact info? (optional)",
        "fa": "📱 آیا می‌خواهید اطلاعات تماس اضافه کنید؟ (اختیاری)",
    },
    "add_contact_yes": {"en": "✅ Yes, add contact", "fa": "✅ بله، اضافه کن"},
    "add_contact_skip": {"en": "⏭️ Skip", "fa": "⏭️ رد کردن"},
    "choose_contact_type": {
        "en": "📱 Choose: phone or email",
        "fa": "📱 انتخاب کنید: تلفن یا ایمیل",
    },
    "contact_phone": {"en": "📞 Phone", "fa": "📞 تلفن"},
    "contact_email": {"en": "📧 Email", "fa": "📧 ایمیل"},
    "enter_phone": {
        "en": "📞 Enter your phone number (E.164, e.g. +989123456789)",
        "fa": "📞 شماره تلفن را وارد کنید (مثال: ۹۸۹۱۲۳۴۵۶۷۸۹+)",
    },
    "enter_email": {
        "en": "📧 Enter your email address",
        "fa": "📧 آدرس ایمیل را وارد کنید",
    },
    "invalid_phone": {
        "en": "❌ Invalid phone format. Use E.164 (max 15 digits).",
        "fa": "❌ فرمت تلفن نامعتبر است.",
    },
    "invalid_email": {
        "en": "❌ Invalid email address.",
        "fa": "❌ آدرس ایمیل نامعتبر است.",
    },
    "contact_saved": {
        "en": "✅ Contact info saved.",
        "fa": "✅ اطلاعات تماس ذخیره شد.",
    },
    "contact_received": {
        "en": "✅ Phone number received. You can add email below or skip.",
        "fa": "✅ شماره دریافت شد. می‌توانید ایمیل اضافه کنید یا رد کنید.",
    },
    # ——— Categories (for keyboard) ———
    "category_job": {"en": "💼 Job", "fa": "💼 شغل"},
    "category_rent": {"en": "🏠 Rent", "fa": "🏠 اجاره"},
    "category_events": {"en": "🎉 Events", "fa": "🎉 رویدادها"},
    "category_services": {"en": "🛠️ Services", "fa": "🛠️ خدمات"},
    "category_sale": {"en": "🛒 Sale", "fa": "🛒 فروش"},
    "category_other": {"en": "📌 Other", "fa": "📌 سایر"},
    # ——— Resubmit flow ———
    "resubmit_intro": {
        "en": "📝 Edit & Resubmit: Here is your rejected ad. Send your new text below.",
        "fa": "📝 ویرایش و ارسال مجدد: آگهی رد شده شما در زیر است. متن جدید خود را بفرستید.",
    },
    "resubmit_edit_prompt": {
        "en": "✍️ Send your new ad text (you can copy and edit the text above).",
        "fa": "✍️ متن جدید آگهی را بفرستید (می‌توانید متن بالا را کپی و ویرایش کنید).",
    },
    "resubmit_confirm": {
        "en": "✅ Submit this new version?",
        "fa": "✅ این نسخه جدید ارسال شود؟",
    },
    "resubmit_success": {
        "en": "🎉 Your revised ad has been submitted! We will notify you when it is reviewed.",
        "fa": "🎉 آگهی اصلاح شده شما ثبت شد! پس از بررسی به شما اطلاع می‌دهیم.",
    },
    "resubmit_error_not_found": {
        "en": "❌ This ad could not be found. Please start from the main menu.",
        "fa": "❌ این آگهی یافت نشد. لطفاً از منوی اصلی شروع کنید.",
    },
    "resubmit_error_not_rejected": {
        "en": "❌ This ad is not eligible for resubmission. Please create a new ad from the main menu.",
        "fa": "❌ این آگهی قابل ارسال مجدد نیست. لطفاً از منوی اصلی آگهی جدید ثبت کنید.",
    },
    "resubmit_error_not_yours": {
        "en": "❌ You can only resubmit your own ads. Please use the main menu.",
        "fa": "❌ فقط آگهی‌های خودتان قابل ارسال مجدد هستند. لطفاً از منوی اصلی استفاده کنید.",
    },
    # ——— Errors (generic) ———
    "error_generic": {
        "en": "❌ Something went wrong! Please try again.",
        "fa": "❌ مشکلی پیش آمد! لطفاً دوباره تلاش کنید.",
    },
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
