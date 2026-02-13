# Iraniu — Request Management System

Human-in-the-loop ad request management: AI pre-scan, admin review, multi-channel distribution (Telegram DM, Telegram Channel, Instagram Feed, Instagram Story), and template-based image generation.

---

## Table of Contents

1. [Overview](#overview)
2. [Tech Stack](#tech-stack)
3. [Project Structure](#project-structure)
4. [Setup & Run](#setup--run)
5. [Data Models](#data-models)
6. [Application Flow](#application-flow)
7. [Instagram: Feed vs Story](#instagram-feed-vs-story)
8. [Telegram Integration](#telegram-integration)
9. [URLs & Views](#urls--views)
10. [API Reference](#api-reference)
11. [Settings & Configuration](#settings--configuration)
12. [Security](#security)

---

## Overview

**Iraniu** is a Django CRM that:

- **Accepts ad submissions** from a Telegram bot (FA/EN flow), Partner API (`/api/v1/submit/`), or legacy `/api/submit/`.
- **Runs optional OpenAI moderation** on each submission and suggests approve/reject + reason.
- **Lets staff review** ads from the dashboard (list, filters, detail), edit content, then **Approve**, **Reject**, or **Request Revision**.
- **On approval**, distributes the ad to all configured channels in a background thread:
  - **Telegram DM** — approval/rejection notification to the user.
  - **Telegram Channel** — post with generated image (from AdTemplate) and caption.
  - **Instagram Feed** — post with square/4:5 image and caption (Meta Graph API).
  - **Instagram Story** — 9:16 story image only (separate image, separate API call).
  - **API** — ad becomes available to partners via `/api/v1/list/`.
- **Image generation** is template-based (AdTemplate: background, coordinates, fonts). Feed image (1:1 or 4:5) and Story image (9:16) are generated and stored separately so Feed, Telegram channel, and Story never mix.
- **Public media URLs** — generated images are served under `/media/` without login so Instagram’s crawler can fetch them (e.g. `https://request.iraniu.uk/media/generated_ads/...` and `.../generated_stories/...`).

Sensitive configuration (API keys, bot tokens, Instagram tokens) is stored in the database via **SiteConfiguration** (and optional **InstagramConfiguration**) and edited from the **Settings Hub**.

---

## Tech Stack

| Layer         | Technology |
|---------------|------------|
| Backend      | Django 5.x (Python 3.11+) |
| Database     | SQLite (default); PostgreSQL recommended for production |
| Frontend     | Bootstrap 5, Font Awesome (self-hosted), vanilla JS / AJAX |
| Theme        | Professional Light / Dark (theme preference in settings) |
| Integrations | OpenAI (moderation), Telegram Bot API, Instagram Graph API (Meta) |
| Static/Media | WhiteNoise (static), `/media/` for generated and uploaded files |

**Key dependencies** (see `requirements.txt`): Django, openai, requests, Pillow, python-dotenv, whitenoise, arabic-reshaper, python-bidi (for Persian text in images).

---

## Project Structure

```
Request-Manage-System-1/
├── manage.py
├── requirements.txt
├── README.md
├── iraniu/                    # Django project
│   ├── settings.py
│   ├── urls.py                 # Root URLconf (admin, login, core)
│   └── wsgi.py / asgi.py
├── core/                       # Main app
│   ├── models.py               # SiteConfiguration, AdRequest, Category, AdTemplate,
│   │                            # TelegramBot, TelegramChannel, DeliveryLog, etc.
│   ├── views/                  # Views split by module
│   │   ├── main.py             # Landing, dashboard, requests, detail, settings hub,
│   │   │                        # preview-publish, post-to-instagram, bots, categories
│   │   └── api_v1.py           # Partner API (submit, status, list)
│   ├── urls.py
│   ├── services/               # Business logic
│   │   ├── ad_actions.py       # approve_one_ad, reject_one_ad (triggers delivery)
│   │   ├── delivery.py        # DeliveryService: Telegram DM, Channel, Instagram Feed/Story, API
│   │   ├── image_engine.py    # AdTemplate-based image gen (POST/STORY), ensure_feed_image, ensure_story_image
│   │   ├── instagram_api.py   # get_absolute_media_url, post_to_instagram, Graph API helpers
│   │   ├── instagram_client.py # create_container, publish_media (Feed/Story)
│   │   ├── instagram.py       # InstagramService: format_caption, validate token
│   │   ├── post_manager.py    # distribute_ad (Preview & Publish flow)
│   │   ├── telegram_client.py # send_message, send_photo
│   │   └── ...
│   ├── admin.py
│   ├── context_processors.py
│   ├── middleware.py           # LoginRequiredMiddleware (PUBLIC_PATHS: /, /login/, /api/submit/, /media/, ...)
│   └── migrations/
├── templates/
│   ├── base.html
│   ├── registration/login.html
│   └── core/                  # dashboard, ad_list, ad_detail, preview_publish, settings_hub, etc.
├── static/                     # CSS, fonts (Yekan, Montserrat), vendor/fontawesome
└── media/                      # generated_ads/, generated_stories/, ad_templates/, etc.
```

---

## Setup & Run

1. **Virtual environment and dependencies**

   ```bash
   python -m venv .venv
   .venv\Scripts\activate          # Windows
   # source .venv/bin/activate      # Linux/macOS
   pip install -r requirements.txt
   ```

2. **Environment (optional)**

   Create a `.env` (see `.env.example` if present) with e.g. `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`, `MEDIA_URL`, `INSTAGRAM_BASE_URL` (or rely on SiteConfiguration `production_base_url`).

3. **Migrations and superuser**

   ```bash
   python manage.py migrate
   python manage.py createsuperuser
   ```

4. **Run server**

   ```bash
   python manage.py runserver
   ```

5. Open **http://127.0.0.1:8000**, log in with a **staff** user. Use **Dashboard**, **Requests**, **Settings** (Hub: Instagram, Telegram, Channels, Design, Storage), **Bots**, **Categories**, **Template Manager**, and **Deliveries**.

---

## Data Models

### SiteConfiguration (singleton, pk=1)

Global settings: AI (OpenAI key, model, system prompt), Telegram (legacy bot token/username, webhook), **production_base_url** (HTTPS base for webhooks and Instagram media URLs), default Telegram channel (channel ID, bot, handle), **Instagram Business** (app ID, app secret, business ID, Facebook access token, OAuth state), message templates (approval, rejection, submission ack), theme, default font/watermark/colors, workflow stages, retention, etc. **is_instagram_enabled** is auto-set when all required Instagram fields are filled.

### AdRequest

Per submission: **uuid**, **category** (FK to Category), **status** (pending_ai → pending_manual → approved / rejected / needs_revision / expired / solved), **content**, **rejection_reason**, **ai_suggested_reason**, **contact_snapshot**, **telegram_user_id**, **bot** (FK), **user** (FK to TelegramUser), **approved_at**, **submitted_via_api_client**.

- **generated_image** — ImageField, `upload_to='generated_ads/'`; used for **Feed** and **Telegram channel**.
- **generated_story_image** — ImageField, `upload_to='generated_stories/'`; used only for **Instagram Story** (never mixed with Feed).
- **instagram_post_id**, **instagram_story_id** — IDs returned by Meta after publishing.
- **is_instagram_published** — True when at least one of Feed or Story was published.

### Category

Dynamic categories (name, name_fa, slug, color, icon, is_active, order). Used in bot flow and ad list filters.

### AdTemplate

Template for ad image generation: background image, font file, **coordinates** (JSON: category, description, phone — x, y, size, color, align, etc.), **story_coordinates** (for 9:16). One active template is used by the image engine for both Feed and Story (Story uses Y-offset from post coordinates).

### TelegramBot, TelegramChannel, TelegramUser, TelegramSession

Multi-bot support (token encrypted, webhook or polling, environment PROD/DEV). Channels link to a bot and optional SiteConfiguration. Sessions hold per-user state (language, draft).

### DeliveryLog

Per-ad, per-channel delivery result (telegram, telegram_channel, instagram, instagram_story, api): status (pending/success/failed), response_payload, error_message.

### Others

InstagramConfiguration (optional per-account tokens), ScheduledInstagramPost, ApiClient (Partner API), AdminProfile (staff + Telegram ID for notifications), SystemStatus, Notification, ActivityLog, VerificationCode.

---

## Application Flow

1. **Ingress** — User submits via Telegram bot (FA/EN flow) or Partner API `POST /api/v1/submit/` (or legacy `/api/submit/`). AdRequest is created with status **Pending AI**.
2. **AI moderation (if enabled)** — `run_ai_moderation()` returns approve/reason; status → **Pending Manual**; reason stored in `ai_suggested_reason` if reject suggested.
3. **Admin** — Staff see **Requests** (filters: category, status, date, search). From **Detail** they can edit content, then **Approve**, **Reject**, or **Request Revision**.
4. **Approve** — `approve_one_ad()` sets status → Approved, `approved_at`, then starts a **background thread** that runs **DeliveryService.send(ad, channel)** for each channel: telegram, telegram_channel, instagram, instagram_story, api.
5. **Delivery**
   - **Telegram DM** — approval message to user (localized).
   - **Telegram Channel** — ensure Feed image (`ensure_feed_image`), upload photo + caption via bot.
   - **Instagram Feed** — ensure Feed image, build public URL (`get_absolute_media_url(ad.generated_image)`), create container + publish; save `instagram_post_id`, `is_instagram_published`.
   - **Instagram Story** — ensure Story image (`ensure_story_image`), build public URL (`get_absolute_media_url(ad.generated_story_image)`), create container with `media_type=STORIES` + publish; save `instagram_story_id`, `is_instagram_published`.
   - **API** — ad is simply available at `/api/v1/list/` (no outbound call).
6. **Reject** — Status → Rejected, message to user with optional “Edit & Resubmit” button (`https://t.me/<bot>?start=resubmit_<uuid>`).

---

## Instagram: Feed vs Story

- **Strict separation:** Feed uses **generated_image** (saved under `media/generated_ads/`). Story uses **generated_story_image** (saved under `media/generated_stories/`). Two generation paths (POST 1080×1350, STORY 1080×1920), two API calls, two media IDs.
- **Public URLs:** Instagram requires a **public, absolute** image URL (e.g. `https://request.iraniu.uk/media/generated_ads/xxx.png`). Base URL is taken from `INSTAGRAM_BASE_URL` (settings/env), then **SiteConfiguration.production_base_url**, then default **https://request.iraniu.uk**. Helper: **get_absolute_media_url(file_field)** in `core.services.instagram_api`.
- **No login for media:** `/media/` is in **PUBLIC_PATHS** in `core.middleware.LoginRequiredMiddleware`, so Meta’s crawler gets **200 OK** without authentication.
- **Flow:** `ensure_feed_image(ad)` / `ensure_story_image(ad)` generate and attach the file to the model if missing. Then `get_absolute_media_url(ad.generated_image)` or `..._story_image` is passed to **create_container** (image_url, caption for Feed only, media_type=STORIES for Story), then **publish_media(creation_id)**. Success is recorded in **DeliveryLog** and in `ad.instagram_post_id` / `ad.instagram_story_id` and `ad.is_instagram_published`.
- **Manual publish:** From **Request Detail**, **Preview & Publish** runs `distribute_ad()` (Telegram + Instagram Feed + Story). Separate buttons **Feed** / **Story** call `post_to_instagram_view` with target `feed` or `story`, using the same ensure + get_absolute_media_url + post_to_instagram flow.

---

## Telegram Integration

- **Bots:** Multiple bots (Django admin + Bots UI). Token stored encrypted; webhook or polling; environment PROD/DEV. Webhook URL uses **production_base_url** and optional secret token.
- **Channels:** TelegramChannel links a channel ID to a bot. SiteConfiguration can define a default channel (telegram_channel_id + default_telegram_bot). On approval, the Feed image is sent to the channel with a Persian caption (category, description, phone, hashtags).
- **Flow:** User talks to bot → session state (language, contact, content, category) → submit → AdRequest created. On approve/reject, user gets a DM; on approve, channel gets the generated image.
- **Runner:** Bots can auto-start with Django (default) or run via `python manage.py runbots` (e.g. for polling workers). Set `ENABLE_AUTO_BOTS=false` to disable auto-start. **TELEGRAM_MODE**: `polling` (getUpdates) or `webhook` (Telegram POSTs to your server).

---

## URLs & Views

| Path | Description |
|------|-------------|
| `/` | Landing; staff → dashboard |
| `/dashboard/` | KPIs, pulse, charts |
| `/dashboard/channels/` | Channel list, create, delete, set default, test |
| `/requests/` | Ad list, filters, bulk actions |
| `/requests/<uuid>/` | Ad detail, approve/reject/request revision, **Preview & Publish**, **Feed** / **Story** buttons |
| `/requests/<uuid>/preview-publish/` | Preview image + caption, then “Confirm & Distribute” |
| `/requests/<uuid>/post-to-instagram/<feed\|story>/` | POST; publish to Instagram Feed or Story (JSON response) |
| `/bots/` | Bot list, create, edit, test, webhook, start/stop |
| `/settings/`, `/settings/hub/instagram/`, `.../telegram/`, `.../channels/`, `.../design/`, `.../storage/` | Settings Hub (Instagram API, Telegram, Channels, Design, Storage) |
| `/settings/check-instagram/`, `.../instagram/connect/`, `.../callback/`, `.../check-permissions/` | Instagram token check and OAuth |
| `/categories/` | Category CRUD |
| `/templates/`, `/templates/tester/` | AdTemplate manager and tester |
| `/deliveries/` | Delivery log list, retry |
| `/admin-management/` | Admin profiles (Telegram ID for notifications) |
| `/api/submit/` | Legacy submit (public) |
| `/api/v1/submit/`, `/api/v1/status/<uuid>/`, `/api/v1/list/` | Partner API (X-API-KEY) |
| `/api/approve/`, `/api/reject/`, `/api/bulk-approve/`, `/api/bulk-reject/`, `/api/pulse/` | Staff APIs |
| `/api/instagram/post/` | Staff: post to Instagram |
| `/telegram/webhook/<bot_id>/`, `.../<uuid>/` | Telegram webhook |

Django admin: `/admin/`. Login/logout: `/login/`, `/logout/`.

---

## API Reference

### Submit (public)

- **POST /api/submit/** — Legacy: `content`, optional `category`, `telegram_user_id`, `telegram_username`, `raw_telegram_json`. Returns `{ status, uuid, ack_message }`.
- **POST /api/v1/submit/** — Partner API: `X-API-KEY` header; body with ad content and optional category/contact. Returns `{ status, uuid, ... }`.

### Staff (authenticated)

- **POST /api/approve/** — `{ "ad_id": "<uuid>", "content": "optional" }`. Sets Approved and triggers full delivery.
- **POST /api/reject/** — `{ "ad_id": "<uuid>", "reason": "..." }`.
- **POST /api/bulk-approve/** — `{ "ad_ids": ["<uuid>", ...] }` (e.g. up to 50).
- **POST /api/bulk-reject/** — `{ "ad_ids": [...], "reason": "..." }`.
- **GET /api/pulse/** — Live stats for dashboard.

### Partner API (X-API-KEY)

- **GET /api/v1/list/** — List approved ads (for partners).
- **GET /api/v1/status/<uuid>/** — Status of one ad.

---

## Settings & Configuration

**Settings Hub** (staff):

- **Instagram API** — App ID, App Secret, Business ID, Facebook access token; OAuth connect/callback; production_base_url (used for media URLs if set). Token check and permission check endpoints.
- **Telegram** — Legacy bot token/username; production_base_url for webhook.
- **Channels** — Default channel ID, title, handle, bot; link to Channel Manager.
- **Design** — Default font, watermark, primary/secondary/accent colors; theme (Light/Dark).
- **Storage** — Retention, cleanup days; cleanup-generated-media action; export/import config (no plain secrets in export).

Other: Categories, Template Manager (AdTemplate coordinates and story coordinates), Bots (multi-bot, webhook, start/stop), Admin Management (staff Telegram IDs for new-request notifications), API (Partner API keys).

---

## Security

- **Staff-only:** Dashboard, Requests, Detail, Settings, Bots, Categories, Templates, Deliveries, and approve/reject/pulse/export/import require staff.
- **Public paths (no login):** `/`, `/login/`, `/logout/`, `/i18n/`, `/api/submit/`, `/api/v1/` (X-API-KEY), `/telegram/webhook/`, **`/media/`** (so Instagram and other crawlers can load images).
- **CSRF:** Enabled for browser requests; exempt for `/api/submit/`, webhooks, and Partner API where appropriate.
- **Secrets:** Stored in DB (encrypted where applicable). Use env and secret management in production; do not commit real keys.
- **Allowed hosts / CSRF origins:** Set `DJANGO_ALLOWED_HOSTS` and `DJANGO_CSRF_TRUSTED_ORIGINS` (e.g. `https://request.iraniu.uk`).

---

This README describes the full Iraniu product: ad lifecycle, AI moderation, multi-channel delivery (Telegram DM, Telegram Channel, Instagram Feed, Instagram Story, API), template-based image generation with strict Feed/Story separation, and public media URLs for Instagram.
