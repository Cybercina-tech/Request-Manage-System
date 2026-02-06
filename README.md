# Iraniu — Request Management System

Human-in-the-loop ad request management: AI pre-scan, admin review, and Telegram notifications.

---

## Table of Contents

1. [Overview](#overview)
2. [Tech Stack](#tech-stack)
3. [Project Structure](#project-structure)
4. [Setup & Run](#setup--run)
5. [Data Models & Database](#data-models--database)
6. [Application Flow](#application-flow)
7. [URLs & Views](#urls--views)
8. [API Reference](#api-reference)
9. [AI Moderation (OpenAI)](#ai-moderation-openai)
10. [Telegram Integration](#telegram-integration)
11. [Settings & Configuration](#settings--configuration)
12. [Security](#security)
13. [Icons & UI Assets](#icons--ui-assets)

---

## Overview

**Iraniu** is a Django web application that:

- Accepts **ad submissions** (e.g. from a Telegram bot or any HTTP client).
- Optionally runs **OpenAI-based moderation** on each submission and suggests approve/reject + reason.
- Lets **staff admins** review, approve, or reject ads from a dashboard.
- Sends **Telegram notifications** to users when their ad is approved or rejected (with optional “Edit & Resubmit” link).

All sensitive configuration (API keys, bot token, message templates) is stored in the database via a singleton **SiteConfiguration** and edited from the **Settings** page.

---

## Tech Stack

| Layer        | Technology                          |
|-------------|--------------------------------------|
| Backend     | Django 5.0.1 (Python 3.11+)         |
| Database    | SQLite (default)                     |
| Frontend    | Bootstrap 5, Font Awesome (self-hosted), Vanilla JS / AJAX |
| Theme       | Midnight Obsidian & Electric Soul (dark)    |
| Integrations| OpenAI API (moderation), Telegram Bot API   |

**Dependencies** (see `requirements.txt`):

- `Django==5.0.1`
- `openai>=1.0.0`
- `requests>=2.31.0`

---

## Project Structure

```
Request-Manage-System/
├── manage.py                 # Django CLI entry
├── requirements.txt          # Python dependencies
├── README.md                 # This file
├── iraniu/                   # Django project package
│   ├── __init__.py
│   ├── settings.py           # App list, DB, static, auth, etc.
│   ├── urls.py               # Root URLconf (admin, login, core)
│   ├── asgi.py / wsgi.py
├── core/                     # Main app: ads + config + services
│   ├── __init__.py
│   ├── models.py             # SiteConfiguration, AdRequest
│   ├── views.py              # Landing, Dashboard, Requests, Settings, APIs
│   ├── urls.py               # All core routes
│   ├── services/              # Business logic (dashboard, telegram, ai_moderation, ad_actions, …)
│   ├── admin.py              # Django admin for SiteConfiguration, AdRequest
│   ├── context_processors.py # Injects config into templates
│   └── migrations/
├── templates/
│   ├── base.html             # Layout, nav, static refs
│   ├── registration/
│   │   └── login.html
│   └── core/
│       ├── landing.html      # Public entry + login link
│       ├── dashboard.html    # KPIs, pulse, 7-day chart
│       ├── ad_list.html      # Requests list, filters, bulk actions
│       ├── ad_detail.html   # Single ad, approve/reject, edit content
│       └── settings.html    # AI, Telegram, Messages tabs
└── static/
    ├── css/
    │   └── iraniu.css       # Custom dark theme
    └── vendor/
        └── fontawesome/     # Self-hosted Font Awesome (css, webfonts, svgs)
```

---

## Setup & Run

1. **Create and activate a virtual environment:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate   # Windows: .venv\Scripts\activate
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Run migrations:**

   ```bash
   python manage.py migrate
   ```

4. **Create a staff/superuser:**

   ```bash
   python manage.py createsuperuser
   ```

5. **Start the server:**

   ```bash
   python manage.py runserver
   ```

6. Open **http://127.0.0.1:8000**. Log in with a **staff** user to access Dashboard, Requests, and Settings.

---

## Data Models & Database

### SiteConfiguration (singleton)

Single row (pk=1) holding global settings.

| Field | Purpose |
|-------|--------|
| `is_ai_enabled` | Turn OpenAI moderation on/off |
| `openai_api_key` | OpenAI API key |
| `openai_model` | Model name (e.g. gpt-3.5-turbo, gpt-4o) |
| `ai_system_prompt` | System prompt for moderation; response must be JSON `{"approved": bool, "reason": "..."}` |
| `telegram_bot_token` | Bot token for sending messages |
| `telegram_bot_username` | Bot username (no @) for “Edit & Resubmit” deep link |
| `telegram_webhook_url` | Optional webhook URL |
| `use_webhook` | Whether webhook is used |
| `approval_message_template` | Message on approve; `{ad_id}` placeholder |
| `rejection_message_template` | Message on reject; `{reason}`, `{ad_id}` |
| `submission_ack_message` | Message sent back after submit (e.g. “under review”) |
| `updated_at` | Last config change |

**Singleton behavior:** Only one row is ever created; `get_config()` returns that row.

### AdRequest

One row per ad submission.

| Field | Purpose |
|-------|--------|
| `uuid` | Public unique ID (UUID, indexed) |
| `category` | One of: job_vacancy, rent, events, services, sale, other |
| `status` | pending_ai → pending_manual → approved / rejected (or expired, solved) |
| `content` | Main ad text (HTML/Markdown stripped before save) |
| `rejection_reason` | Set when admin rejects; can be sent to user |
| `ai_suggested_reason` | Reason from OpenAI when AI suggests reject |
| `telegram_user_id` | Telegram user ID for notifications |
| `telegram_username` | Telegram username (optional) |
| `raw_telegram_json` | Optional raw payload from Telegram |
| `created_at`, `updated_at` | Timestamps |
| `approved_at` | Set when status becomes approved |

**Status flow:**

- **Pending AI** — Just created; if AI is enabled, moderation runs and status moves to Pending Manual (with optional `ai_suggested_reason`).
- **Pending Manual** — Waiting for admin to approve or reject.
- **Approved** — Approved; user notified via Telegram.
- **Rejected** — Rejected with reason; user notified (with “Edit & Resubmit” button if bot username is set).
- **Expired / Solved** — For future use (e.g. expiry or resolution).

**Indexes:** `status`, `created_at`, `(category, status)` for fast filters and dashboards.

---

## Application Flow

1. **Ingress**  
   Client (e.g. Telegram bot) POSTs to `/api/submit/` with `content`, optional `category`, `telegram_user_id`, `telegram_username`, `raw_telegram_json`.

2. **Create AdRequest**  
   New `AdRequest` is created with `status=PENDING_AI`, `content` cleaned (HTML/Markdown stripped).

3. **AI (if enabled)**  
   If `SiteConfiguration.is_ai_enabled` and API key is set, `run_ai_moderation()` is called. Response is parsed as JSON `{approved, reason}`. Status is set to `PENDING_MANUAL`; if not approved, `ai_suggested_reason` is stored. AI never auto-deletes; admin always has final say.

4. **Response to client**  
   API returns `{ status: "created", uuid, ack_message }`. The bot can send `ack_message` (e.g. submission ack) to the user.

5. **Admin**  
   Staff open **Dashboard** (KPIs, pulse, 7-day chart) and **Requests** (list with filters: category, status, date, search). From **Detail** they can edit content, then Approve or Reject.

6. **Approve**  
   Status → Approved, `approved_at` set, Telegram message sent using `approval_message_template` with `{ad_id}`.

7. **Reject**  
   Status → Rejected, `rejection_reason` set, Telegram message sent using `rejection_message_template` with `{reason}` and `{ad_id}`, plus inline “Edit & Resubmit” button linking to `https://t.me/<bot_username>?start=resubmit_<uuid>`.

---

## URLs & Views

| URL | View | Auth | Description |
|-----|------|------|-------------|
| `/` | `landing` | — | Public landing; redirects staff to dashboard |
| `/dashboard/` | `dashboard` | Staff | KPIs, pulse score, 7-day submission chart |
| `/requests/` | `ad_list` | Staff | Paginated list, filters (category, status, date, search), bulk approve/reject |
| `/requests/<uuid>/` | `ad_detail` | Staff | Single ad: content, audit info, edit, approve/reject |
| `/settings/` | `settings_view` | Staff | Tabs: AI, Telegram, Messages (templates) |
| `/settings/save/` | `settings_save` | Staff | POST: save configuration |
| `/settings/test-telegram/` | `test_telegram` | Staff | POST: test bot token (getMe) |
| `/settings/test-openai/` | `test_openai` | Staff | POST: test OpenAI key |
| `/settings/export/` | `export_config` | Staff | GET: export config as JSON (no secrets in plain text) |
| `/settings/import/` | `import_config` | Staff | POST: import config JSON |
| `/api/approve/` | `approve_ad` | Staff | POST: approve one ad (optional edited content) |
| `/api/reject/` | `reject_ad` | Staff | POST: reject with reason |
| `/api/bulk-approve/` | `bulk_approve` | Staff | POST: list of ad_ids |
| `/api/bulk-reject/` | `bulk_reject` | Staff | POST: ad_ids + single reason |
| `/api/pulse/` | `api_pulse` | Staff | GET: live stats for dashboard polling |
| `/api/submit/` | `submit_ad` | — | POST: create ad (Telegram/external); CSRF exempt |

Django built-in:

- `/admin/` — Django admin (SiteConfiguration, AdRequest).
- `/login/`, `/logout/` — Auth (LOGIN_REDIRECT_URL → dashboard).

---

## API Reference

### Submit ad (ingress)

**POST** `/api/submit/`

**Body (JSON or form):**

- `content` (required): Ad text.
- `category` (optional): One of `rent`, `job_vacancy`, `events`, `services`, `sale`, `other`. Default `other`.
- `telegram_user_id` (optional): Telegram user ID.
- `telegram_username` (optional): Telegram username.
- `raw_telegram_json` (optional): Any JSON blob (stored as-is).

**Response:** `200`  
`{ "status": "created", "uuid": "<uuid>", "ack_message": "..." }`

**Errors:** `400` if `content` missing; `500` on server error.

---

### Approve (staff)

**POST** `/api/approve/`

**Body (JSON):** `{ "ad_id": "<uuid>", "content": "optional edited text" }`

**Response:** `200` `{ "status": "success" }`  
Ad is set to Approved, approval message sent to user if `telegram_user_id` is set.

---

### Reject (staff)

**POST** `/api/reject/`

**Body (JSON):** `{ "ad_id": "<uuid>", "reason": "..." }`

**Response:** `200` `{ "status": "success" }`  
Ad is set to Rejected, rejection message + “Edit & Resubmit” button sent if applicable.

---

### Bulk approve / reject (staff)

- **POST** `/api/bulk-approve/`: `{ "ad_ids": ["<uuid>", ...] }` — up to 50.
- **POST** `/api/bulk-reject/`: `{ "ad_ids": ["<uuid>", ...], "reason": "..." }` — up to 50.

---

### Pulse (live stats, staff)

**GET** `/api/pulse/`

**Response:**  
`{ "total", "pending_ai", "pending_manual", "approved_today", "rejected_today", "rejection_rate", "pulse_score", "system_health" }`  
Used by the dashboard for auto-refresh.

---

## AI Moderation (OpenAI)

- **Where:** `core.services.run_ai_moderation(content, config)`.
- **When:** Right after an ad is created in `submit_ad`, if `config.is_ai_enabled` and `config.openai_api_key` are set.
- **Input:** `content` (cleaned ad text, up to 4000 chars) and `config` (SiteConfiguration).
- **Prompt:** `config.ai_system_prompt` (must ask for JSON `{"approved": true/false, "reason": "..."}`).
- **Output:** `(approved: bool, reason: str)`. Reason is truncated to 500 chars and stored in `ai_suggested_reason` when not approved.
- **Behavior:** Never deletes the ad. Status is always set to `PENDING_MANUAL`; if AI says reject, admin still sees the ad and can approve or reject. Failures (e.g. API error) default to `(True, "")` so the ad stays in queue.

---

## Telegram Integration

- **Sending:** Uses Telegram Bot API `sendMessage` (and optional `reply_markup` for inline keyboard).
- **Config:** `telegram_bot_token`, `telegram_bot_username` (for “Edit & Resubmit” link).
- **When:**  
  - After **approve:** `send_telegram_message(chat_id, approval_message_template.format(ad_id=...), config)`.  
  - After **reject:** `send_telegram_rejection_with_button(chat_id, rejection_message_template.format(...), ad_uuid, config)` — adds inline button with `https://t.me/<username>?start=resubmit_<uuid>`.
- **Testing:** Settings page can POST to `/settings/test-telegram/` with a token to run `getMe` (token is not saved by that request).
- **Update handling:** **Webhook only** (no polling). Telegram sends POST to your server; no separate bot process.
- **Endpoint:** `POST /telegram/webhook/<bot_id>/` — receives updates, runs conversation engine, sends replies.
- **How to run the bot:** (1) Run Django with **HTTPS**. (2) In **Bots** → Create/Edit, set **Webhook URL** to `https://<your-domain>/telegram/webhook/<bot_id>/` and save — the app auto-calls `setWebhook`. (3) Send `/start` to the bot; you should get the language selection. If the bot does not respond, ensure the webhook URL is saved (so Telegram receives it), the server is reachable over HTTPS, and the bot is Active. Use `python manage.py check_telegram --bot-id 1` to test.

### Telegram Bot Runner

The `runbots` management command provides a managed, production-safe way to run all active Telegram bots.

#### How to start

```bash
python manage.py runbots [--log-dir=logs]
```

This starts the supervisor, which:

- Loads all active bots (`is_active=True`)
- Starts one worker process per polling bot
- Monitors workers and auto-restarts crashed ones
- Updates `last_heartbeat`, `status`, and `worker_pid` in the DB
- Handles SIGTERM/SIGINT for graceful shutdown

#### Command options

| Option | Description |
|--------|-------------|
| `--log-dir=DIR` | Directory for `bot_<id>.log` files (default: `logs/`) |
| `--once` | Run a single supervisor tick and exit |
| `--bot-id=N` | Run only bot ID N (can repeat: `--bot-id=1 --bot-id=2`) |
| `--debug` | Enable debug logging |

#### Dev vs Prod

- **Dev:** Use **Polling** mode (`TELEGRAM_MODE=polling`, default). No HTTPS required. `runbots` starts workers that long-poll `getUpdates`.
- **Prod:** Use **Webhook** mode (`TELEGRAM_MODE=webhook`) when you have HTTPS. `runbots` only validates webhook + runs health checker; no polling workers. Telegram POSTs updates to your server.

#### Polling vs Webhook

| Mode | Behavior |
|------|----------|
| **Polling** | Workers fetch updates via `getUpdates`; logs to `logs/bot_<id>.log` |
| **Webhook** | No workers; validate HTTPS webhook, run health checks every 30s |

#### Settings

```python
# iraniu/settings.py (or env TELEGRAM_MODE)
TELEGRAM_MODE = "polling"  # or "webhook"
```

#### Systemd example

```ini
[Unit]
Description=Iraniu Telegram bot runner
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/path/to/project
ExecStart=/path/to/venv/bin/python manage.py runbots --log-dir=/var/log/iraniu
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

#### Supervisor example

```ini
[program:iraniu-runbots]
command=/path/to/venv/bin/python manage.py runbots --log-dir=/var/log/iraniu
directory=/path/to/project
user=www-data
autostart=true
autorestart=true
stdout_logfile=/var/log/iraniu/runbots.log
stderr_logfile=/var/log/iraniu/runbots.err
```

#### Bots page

From **Bots** you can:

- **Status** — online, offline, or error
- **PID** — worker process ID when running
- **Last heartbeat** — last update timestamp (stale > 90s → offline)
- **Last error** — last error message (cleared on success)
- **Start** / **Stop** / **Restart** — request actions (applied on next supervisor tick)
- **Test token** — verify bot connection

---

## Settings & Configuration

All editable in **Settings** (and optionally Django admin):

- **AI:** Enable/disable, API key, model, system prompt.
- **Telegram:** Bot token, bot username, webhook URL, use_webhook.
- **Messages:** Approval template, rejection template, submission ack message.

Templates support placeholders: `{ad_id}`, `{reason}`. Export/Import are JSON (no secret values in export).

---

## Security

- **Staff-only:** Dashboard, Requests, Detail, Settings, and all approve/reject/pulse/export/import endpoints use `@staff_member_required`.
- **CSRF:** All form and AJAX requests from the site use CSRF token. Submit API is `@csrf_exempt` for external/Telegram callers; in production, protect by firewall or additional auth if needed.
- **Secrets:** Stored in DB (SiteConfiguration). Do not commit real keys; use environment variables or secret management in production.
- **DEBUG / ALLOWED_HOSTS:** Controlled via env: `DEBUG`, `ALLOWED_HOSTS`. `SECRET_KEY` via `DJANGO_SECRET_KEY`.
- **Project settings:** Set `DJANGO_SETTINGS_MODULE=iraniu.settings` in `.env` or your environment when using runserver, WSGI, or management commands (e.g. in production or CI).

---

## Icons & UI Assets

Icons are **bundled locally** using [Font Awesome](https://fontawesome.com/) (free) — **no CDN required**. This avoids blocked fonts on Safari/macOS and keeps the app working offline.

- **Location:** `static/vendor/fontawesome/` (css, webfonts, svgs). Included in the repo and served via Django `staticfiles`.
- **Usage:** In templates use Font Awesome v6 classes, e.g. `<i class="fa-solid fa-inbox icon" aria-hidden="true"></i>`. The base layout loads `vendor/fontawesome/css/all.min.css` before custom CSS.
- **Cache busting:** Static assets use `?v={{ STATIC_VERSION }}` (set via `STATIC_VERSION` env or default `1`). Run `collectstatic` for production.
- **Optional SVG:** For inline SVG icons (e.g. in dashboards), place SVGs under `templates/icons/` and use `{% include "icons/play.svg" %}`. Font Awesome webfonts remain the primary icon set.

Verify icon rendering in Safari, Chrome, and Firefox; self-hosted fonts avoid CSP and third-party blocking issues.

---

This README describes the full Iraniu request management program: data models, flow, URLs, APIs, AI moderation, Telegram usage, and configuration in one place.
