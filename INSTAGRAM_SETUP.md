# Instagram Graph API — Setup & Usage

Automated Instagram posting via the official Meta Graph API. Supports Business/Creator accounts linked to a Facebook Page.

---

## 1. Prerequisites

- **Business or Creator Instagram account** linked to a **Facebook Page**
- **Facebook Developer App** with Instagram Graph API permissions
- **Long-lived access token** (never short-lived user token)

---

## 2. Create Facebook Developer App & Get Token

### Step 1: Create App

1. Go to [developers.facebook.com](https://developers.facebook.com/)
2. **My Apps** → **Create App** → **Business** type
3. Add **Instagram Graph API** product

### Step 2: Get Page Access Token

1. **Tools** → **Graph API Explorer**
2. Select your app, add permissions:
   - `instagram_basic`
   - `instagram_content_publish`
   - `pages_show_list`
   - `pages_read_engagement`
3. **Generate Access Token** (User Token)
4. Exchange for **Page Access Token** (long-lived):
   - GET `https://graph.facebook.com/v18.0/me/accounts?access_token=USER_TOKEN`
   - Copy `access_token` of the Page linked to your Instagram

### Step 3: Exchange for Long-Lived Token

```
GET https://graph.facebook.com/v18.0/oauth/access_token?
  grant_type=fb_exchange_token&
  client_id=APP_ID&
  client_secret=APP_SECRET&
  fb_exchange_token=PAGE_ACCESS_TOKEN
```

Use the returned `access_token` in Django (encrypted at rest).

### Step 4: Get Instagram User ID

- GET `https://graph.facebook.com/v18.0/me/accounts?fields=instagram_business_account&access_token=TOKEN`
- Or use the **Test connection** in Django admin → Instagram settings; it auto-fills `ig_user_id`

---

## 3. Environment Variables (.env)

```env
# Required for Instagram
INSTAGRAM_BASE_URL=https://yourdomain.com
MEDIA_URL=/media/

# Optional: use placeholder image URL in settings instead
# Placeholder must be publicly accessible (Instagram fetches it)
```

**Important:**  
- `INSTAGRAM_BASE_URL` must be the public base URL of your site (e.g. `https://iraniu.example.com`).  
- Images posted to Instagram must be publicly accessible. For dynamic images saved to `media/`, ensure your server serves `/media/` publicly.

---

## 4. Django Configuration

### Add Instagram Config (Admin)

1. Go to **Settings** → **Instagram**
2. Create configuration:
   - **Username:** Instagram username
   - **Access token:** Long-lived token (encrypted at rest)
   - **Page ID:** Facebook Page ID (optional)
   - **Instagram user ID:** Auto-filled on Test
   - **Placeholder image URL:** Default image for caption-only ads (must be public URL)
   - **Active:** checked

### Media & Static

- `MEDIA_ROOT` / `MEDIA_URL` are set in settings
- In production, serve `/media/` via nginx/CDN or S3

---

## 5. Posting Methods

### A. Automatic (Approved Ads)

When an ad is approved, the delivery layer posts to Instagram if:
- Instagram config is active
- Ad has `contact_snapshot` (phone, email)
- Placeholder or ad image URL is set

Caption format: message, email, phone, branding (EN/FA).

### B. API Endpoint (Staff-Only)

**POST** `/api/instagram/post/` (staff login required)

**Immediate post:**
```json
{
  "image_url": "https://example.com/image.png",
  "caption": "Your caption here"
}
```

**With message, email, phone:**
```json
{
  "image_url": "https://example.com/image.png",
  "message_text": "Apartment for rent in Tehran",
  "email": "contact@example.com",
  "phone": "+989123456789",
  "lang": "en"
}
```

**Schedule for later:**
```json
{
  "image_url": "https://example.com/image.png",
  "caption": "...",
  "scheduled_at": "2026-02-10T14:00:00Z"
}
```

### C. Scheduled Posts (Cron)

```bash
# Run every minute
* * * * * cd /path/to/project && python manage.py publish_scheduled_instagram_posts
```

Or add to **Celery Beat** (if using Celery):

```python
# In celery.py / settings
CELERY_BEAT_SCHEDULE = {
    'publish-scheduled-instagram': {
        'task': 'core.tasks.publish_scheduled_instagram_posts_task',
        'schedule': 60.0,  # every 60 seconds
    },
}
```

### D. Admin

- Create **Scheduled Instagram Post** in admin with `image_url`, `caption`, `scheduled_at`
- Management command publishes when `scheduled_at` has passed

---

## 6. Dynamic Image Generation

Use `core.services.instagram_image`:

```python
from core.services.instagram_image import generate_instagram_image, save_generated_image, get_absolute_media_url

# Generate PNG bytes
data = generate_instagram_image(
    message="Apartment for rent",
    email="contact@example.com",
    phone="+989123456789",
    lang="en",
)

# Save to media/instagram/ and get relative URL
rel_url = save_generated_image(
    message="Apartment for rent",
    email="contact@example.com",
    phone="+989123456789",
    lang="en",
)

# Build absolute URL for Instagram (must be public)
abs_url = get_absolute_media_url(rel_url)
# Use abs_url in post_custom or ScheduledInstagramPost
```

**Requirements:**  
- Pillow installed (`pip install Pillow`)
- `INSTAGRAM_BASE_URL` set
- `/media/` served publicly

---

## 7. Sample Captions

### English

```
📂 [Rent]

Cozy 2BR apartment in Tehran, near metro. Available March.

📧 contact@example.com
📞 +989123456789

🙏 Iraniu — trusted classifieds
```

### Persian (فارسی)

```
📂 [اجاره]

آپارتمان دو خوابه در تهران، نزدیک مترو. از اسفند ماه.

📧 contact@example.com
📞 +989123456789

🙏 ایرانيو — آگهی‌های معتبر
```

---

## 8. Error Handling & Logging

- **Retries:** 3 attempts with 2-second delay on network errors
- **Logging:** `core.services.instagram` logs success/failure
- **DeliveryLog:** Per-ad delivery status in admin
- **ScheduledInstagramPost:** `error_message` populated on failure

---

## 9. Security

- Access tokens stored **encrypted** (Fernet, SECRET_KEY-derived)
- Use `.env` or environment variables; never hardcode credentials
- Instagram config in admin excludes `access_token_encrypted` from display

---

## 10. Instagram Image Requirements

- Min width: 320px
- Max width/height: 1080px
- Aspect ratio: 1:1 (square) or 4:5 (portrait)
- Format: JPEG or PNG
- `image_url` must be **publicly accessible** (HTTPS preferred)
