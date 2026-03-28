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
    # ——— Main menu (friendly greeting + intro) ———
    "main_menu": {
        "en": "📋 Main menu",
        "fa": "📋 منوی اصلی",
    },
    "main_menu_greeting": {
        "en": "👋 Hello! Welcome to Iraniu.\n\n"
        "We help you publish classified ads safely and reach the right audience. "
        "Our platform offers categories for jobs, rent, events, services, and more.\n\n"
        "🔒 Your data is protected and we review ads to keep our community safe.\n"
        "✅ You can trust Iraniu for professional, reliable classifieds.\n\n"
        "Choose an option below:",
        "fa": "👋 سلام! به ایرانيو خوش آمدید.\n\n"
        "ما به شما کمک می‌کنیم آگهی‌های خود را به‌صورت امن منتشر کنید و به مخاطب درست برسید. "
        "دسته‌بندی‌های شغل، اجاره، رویدادها، خدمات و غیره در اختیار شماست.\n\n"
        "🔒 اطلاعات شما محافظت می‌شود و آگهی‌ها بررسی می‌شوند تا جامعه ما امن بماند.\n"
        "✅ می‌توانید برای آگهی‌های حرفه‌ای و معتبر به ایرانيو اعتماد کنید.\n\n"
        "یکی از گزینه‌های زیر را انتخاب کنید:",
    },
    "btn_about_us": {
        "en": "ℹ️ About us",
        "fa": "ℹ️ درباره ما",
    },
    "btn_my_ads": {
        "en": "📋 My Ads",
        "fa": "📋 آگهی‌های من",
    },
    "btn_back_to_home": {
        "en": "🏠 Back to Home",
        "fa": "🏠 بازگشت به خانه",
    },
    "about_us_message": {
        "en": "ℹ️ **Iraniu** — Your trusted classifieds platform.\n\n"
        "• **Who we are:** We connect people with the right opportunities through safe, reviewed ads.\n"
        "• **What we do:** Jobs, rent, events, services, sale, and more in one place.\n"
        "• **Security:** We review content and protect your data. Only quality ads go live.\n"
        "• **Why trust us:** Professional service, clear process, and support when you need it.\n\n"
        "Thank you for choosing Iraniu. 🙏",
        "fa": "ℹ️ **ایرانيو** — پلتفرم مطمئن آگهی‌های شما.\n\n"
        "• **ما کیستیم:** با آگهی‌های امن و بررسی‌شده، افراد را به فرصت‌های درست وصل می‌کنیم.\n"
        "• **چه می‌کنیم:** شغل، اجاره، رویدادها، خدمات، فروش و بیشتر در یک جا.\n"
        "• **امنیت:** محتوا بررسی می‌شود و اطلاعات شما محافظت می‌شود. فقط آگهی‌های باکیفیت منتشر می‌شوند.\n"
        "• **چرا به ما اعتماد کنید:** خدمات حرفه‌ای، فرایند شفاف و پشتیبانی وقتی نیاز دارید.\n\n"
        "از اینکه ایرانيو را انتخاب کردید متشکریم. 🙏",
    },
    "my_ads_intro": {
        "en": "📋 **Your ads**\n\n",
        "fa": "📋 **آگهی‌های شما**\n\n",
    },
    "my_ads_empty": {
        "en": "📋 You haven't posted any ads yet.\n\nCreate one from the main menu when you're ready!",
        "fa": "📋 هنوز آگهی منتشر نکرده‌اید.\n\nوقتی آماده بودید از منوی اصلی یک آگهی ثبت کنید!",
    },
    "my_ads_item": {
        "en": "• {preview} — **{status}**\n",
        "fa": "• {preview} — **{status}**\n",
    },
    "ad_status_approved": {"en": "✅ Approved", "fa": "✅ تأیید شده"},
    "ad_status_pending": {"en": "⏳ Pending", "fa": "⏳ در انتظار"},
    "ad_status_needs_revision": {"en": "📝 Needs revision", "fa": "📝 نیاز به اصلاح"},
    "ad_status_rejected": {"en": "❌ Rejected", "fa": "❌ رد شده"},
    "rejection_reason_label": {"en": "Reason: ", "fa": "دلیل: "},
    "create_new_ad": {
        "en": "✨ Create new ad",
        "fa": "✨ ثبت آگهی جدید",
    },
    # ——— Ad content flow (category first, then text) ———
    "select_category_prompt": {
        "en": "📂 First choose a category for your ad.",
        "fa": "📂 ابتدا دسته‌بندی آگهی را انتخاب کنید.",
    },
    "category_explanation": {
        "en": "📂 Category: {category_name}\n\n"
        "At Iraniu we help you reach the right audience. This category is designed for ads like yours.\n\n"
        "• What we do: We review and publish your ad so it appears to interested users.\n"
        "• How it works: After you send your ad text, we review it and keep you informed of publication status.",
        "fa": "📂 دسته‌بندی: {category_name}\n\n"
        "در ایرانيو به شما کمک می‌کنیم به مخاطب درست برسید. این دسته برای آگهی‌هایی مثل شما طراحی شده است.\n\n"
        "• چه می‌کنیم: آگهی شما را بررسی و منتشر می‌کنیم تا به کاربران علاقه‌مند نمایش داده شود.\n"
        "• چطور کار می‌کند: بعد از ارسال متن آگهی، آن را بررسی می‌کنیم و وضعیت انتشار را به شما اطلاع می‌دهیم.",
    },
    "choose_category": {
        "en": "📂 Choose category",
        "fa": "📂 دسته‌بندی را انتخاب کنید",
    },
    "enter_ad_text": {
        "en": "✍️ Enter your ad text (you can send one message with your full ad).",
        "fa": "✍️ متن آگهی را وارد کنید (یک پیام با متن کامل آگهی بفرستید).",
    },
    "enter_ad_text_prompt": {
        "en": "✍️ Now send your ad text (one message).",
        "fa": "✍️ حالا متن آگهی را بفرستید (یک پیام).",
    },
    "ad_content_validation_error": {
        "en": "⚠️ Emojis, stickers, and GIFs are not allowed in ad messages. Please send a plain text description.",
        "fa": "⚠️ استفاده از ایموجی، استیکر و گیف در متن آگهی مجاز نیست. لطفاً فقط متن ساده بفرستید.",
    },
    "ad_content_too_long": {
        "en": "⚠️ Your ad text is too long. Maximum allowed: 500 characters.\nمتن آگهی شما بیش از حد طولانی است. حداکثر مجاز: ۵۰۰ کاراکتر.",
        "fa": "⚠️ متن آگهی شما بیش از حد طولانی است. حداکثر مجاز: ۵۰۰ کاراکتر.\nYour ad text is too long. Maximum allowed: 500 characters.",
    },
    "ad_content_not_persian": {
        "en": "⚠️ Please write your ad in Persian only.\nلطفاً آگهی خود را فقط به زبان فارسی بنویسید.",
        "fa": "⚠️ لطفاً آگهی خود را فقط به زبان فارسی بنویسید.\nPlease write your ad in Persian only.",
    },
    "enter_ad_text_detailed": {
        "en": "✍️ Write your ad\n\n"
        "Please send your ad in one message. (Maximum 500 characters.)\n\n"
        "Suggestions:\n"
        "• Use a clear title for your ad.\n"
        "• Include important details (location, price, and how to reach you).\n"
        "• Avoid all-Latin wording where possible for a cleaner look.\n\n"
        "We look forward to your message! 🙏",
        "fa": "✍️ متن آگهی را بنویسید\n\n"
        "لطفاً آگهی خود را در یک پیام بفرستید. (حداکثر ۵۰۰ کاراکتر)\n\n"
        "نکات پیشنهادی:\n"
        "• عنوان آگهی را واضح بنویسید.\n"
        "• جزئیات مهم (مکان، قیمت و راه‌های ارتباطی) را ذکر کنید.\n"
        "• از نوشتن کلمات تمام لاتین خودداری کنید تا آگهی ظاهر بهتری داشته باشد.\n\n"
        "منتظر متن شما هستیم! 🙏",
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
    # ——— Contact (mandatory before ad creation) ———
    "ask_contact": {
        "en": "📱 To create ads, we need to verify your phone number. Tap the button below to share it.",
        "fa": "📱 برای ثبت آگهی باید شماره تلفن شما تأیید شود. دکمه زیر را بزنید.",
    },
    "ask_contact_use_button": {
        "en": "⚠️ Please use the button below to share your phone number for verification.",
        "fa": "⚠️ لطفاً برای تأیید شماره تلفن، از دکمه زیر استفاده کنید.",
    },
    "phone_number_saved": {
        "en": "✅ Phone number saved.",
        "fa": "✅ شماره تلفن ذخیره شد.",
    },
    "contact_not_verified": {
        "en": "❌ The shared contact does not belong to your account. Please share your own phone number.",
        "fa": "❌ شماره به‌اشتراک‌گذاری‌شده متعلق به حساب شما نیست. لطفاً شماره خودتان را ارسال کنید.",
    },
    "share_contact_btn": {
        "en": "📱 Share Phone Number",
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
        "en": "❌ Invalid phone. Use up to 20 characters: digits, +, spaces, or dashes.",
        "fa": "❌ شماره تلفن نامعتبر است. حداکثر ۲۰ کاراکتر (اعداد، +، فاصله، خط تیره).",
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
        "en": "✍️ Send your new ad text (you can copy and edit the text above).\n\n"
        "Maximum 500 characters per message.",
        "fa": "✍️ متن جدید آگهی را بفرستید (می‌توانید متن بالا را کپی و ویرایش کنید).\n\n"
        "حداکثر ۵۰۰ کاراکتر در هر پیام.",
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
    # ——— Approval / Rejection notifications (no Ad ID; category + friendly tone) ———
    "notification_approved": {
        "en": "✅ Your ad in 📂 {category} has been approved!\n\nThank you for using Iraniu. 🥳 You can post more ads anytime from the main menu.",
        "fa": "✅ آگهی شما در دسته 📂 {category} تأیید شد!\n\nاز اینکه از ایرانيو استفاده می‌کنید متشکریم. 🥳 هر زمان می‌توانید از منوی اصلی آگهی‌های بیشتر ثبت کنید.",
    },
    "notification_rejected": {
        "en": "❌ Your ad in 📂 {category} was not approved.\n\nReason: {reason}\n\nPlease review and try again — we’re here to help. 💡 Thank you for choosing Iraniu.",
        "fa": "❌ آگهی شما در دسته 📂 {category} تأیید نشد.\n\nدلیل: {reason}\n\nلطفاً بررسی کنید و دوباره ارسال کنید؛ ما اینجا هستیم تا کمک کنیم. 💡 از اینکه ایرانيو را انتخاب کردید متشکریم.",
    },
    "notification_needs_revision": {
        "en": "📝 Your ad in 📂 {category} needs revision.\n\nPlease edit and resubmit using the button below. Thank you for using Iraniu.",
        "fa": "📝 آگهی شما در دسته 📂 {category} نیاز به اصلاح دارد.\n\nلطفاً با دکمه زیر ویرایش و ارسال مجدد کنید. از اینکه از ایرانيو استفاده می‌کنید متشکریم.",
    },
    # ——— My Ads: View / Manage / Delete / Edit ———
    "my_ads_btn_manage": {"en": "View/Manage", "fa": "مشاهده/مدیریت"},
    "ad_detail_category": {"en": "📂 Category:", "fa": "📂 دسته:"},
    "ad_detail_text": {"en": "📝 Text:", "fa": "📝 متن:"},
    "ad_detail_phone": {"en": "📱 Phone:", "fa": "📱 تلفن:"},
    "ad_detail_status": {"en": "Status:", "fa": "وضعیت:"},
    "btn_edit_ad": {"en": "✏️ Edit", "fa": "✏️ ویرایش"},
    "btn_delete_ad": {"en": "❌ Delete", "fa": "❌ حذف"},
    "btn_back_to_list": {"en": "⬅️ Back to List", "fa": "⬅️ بازگشت به لیست"},
    "delete_confirm_text": {
        "en": "Are you sure you want to delete this ad?",
        "fa": "آیا مطمئن هستید که می‌خواهید این آگهی را حذف کنید؟",
    },
    "delete_confirm_yes": {"en": "✅ Yes, Delete", "fa": "✅ بله، حذف شود"},
    "delete_confirm_cancel": {"en": "🚫 Cancel", "fa": "🚫 انصراف"},
    "ad_deleted": {"en": "✅ Ad deleted.", "fa": "✅ آگهی حذف شد."},
    "ad_not_found": {
        "en": "❌ This ad was not found or has already been deleted.",
        "fa": "❌ این آگهی یافت نشد یا قبلاً حذف شده است.",
    },
    "edit_ad_link_msg": {
        "en": "✏️ Edit this ad in your browser:\n{url}",
        "fa": "✏️ این آگهی را در مرورگر ویرایش کنید:\n{url}",
    },
    # ——— Errors (generic) ———
    "error_generic": {
        "en": "❌ Something went wrong! Please try again.",
        "fa": "❌ مشکلی پیش آمد! لطفاً دوباره تلاش کنید.",
    },
}

# Map AdRequest.category value to i18n key for display name (used in approval/rejection notifications)
CATEGORY_MESSAGE_KEYS = {
    "job_vacancy": "category_job",
    "rent": "category_rent",
    "events": "category_events",
    "services": "category_services",
    "sale": "category_sale",
    "other": "category_other",
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


def get_category_display_name(category_value: str, lang: str | None) -> str:
    """Return localized category name for approval/rejection messages."""
    key = CATEGORY_MESSAGE_KEYS.get(category_value or "other", "category_other")
    return get_message(key, lang)
