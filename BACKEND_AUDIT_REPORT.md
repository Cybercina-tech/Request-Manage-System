# Iranio Backend Audit Report

Full audit of the Iranio backend: structure, models, views, services, APIs, admin, security, logging, tests, and migrations. Refactors applied are limited to **cleaning, restructuring, and modularization** — no new features.

---

## PART 1 — Project Structure Audit

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| Apps (iranio, core) | OK | Single Django app `core`; project `iranio` holds settings/urls. |
| manage.py, settings, urls | OK | Standard. LOGIN_URL, LOGIN_REDIRECT_URL, LOGOUT_REDIRECT_URL set. Security headers when `DEBUG=False`. |
| Templates / static | OK | `templates/` (base, core/*, registration/*), `static/css/iranio.css`. |
| Duplicated logic | Addressed | Pulse/dashboard logic was duplicated in `dashboard` and `api_pulse` → moved to `core/services/dashboard.py`. Approve/reject logic duplicated in single + bulk → moved to `core/services/ad_actions.py`. |
| Business logic in views | Addressed | Dashboard/pulse now use `get_dashboard_context()` and `get_pulse_data()`. Approve/reject use `approve_one_ad()` and `reject_one_ad()`. JSON body parsing centralized in `view_utils.parse_request_json()`. |
| Modularization | OK | Single package `core/services/`: dashboard, ad_actions, ai_moderation, telegram, conversation, submit_ad_service, users, otp. |

### Current layout

- **Note:** `core/services` is a package only (no `services.py`). Imports: `from core.services import clean_ad_text, run_ai_moderation, ...` via `__init__.py`; `from core.services.dashboard import get_dashboard_context, get_pulse_data`; etc.

```
core/
├── models.py          # SiteConfiguration, TelegramUser, VerificationCode, AdRequest, TelegramBot, TelegramSession, TelegramMessageLog
├── views.py           # Request/response only; delegates to services
├── view_utils.py      # parse_request_json
├── telegram_views.py  # Webhook; delegates to conversation + users
├── services/
│   ├── __init__.py    # Re-exports from ai_moderation, telegram
│   ├── dashboard.py  # get_pulse_data, get_dashboard_context
│   ├── ad_actions.py  # approve_one_ad, reject_one_ad
│   ├── ai_moderation.py  # clean_ad_text, run_ai_moderation, test_openai_connection
│   ├── telegram.py    # send_telegram_message*, webhook helpers, test_telegram_connection
│   ├── conversation.py
│   ├── submit_ad_service.py
│   ├── users.py
│   └── otp.py
├── admin.py
├── middleware.py
├── encryption.py
├── i18n.py
├── conf.py
└── management/commands/check_bots.py
```

---

## PART 2 — Models & Database

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| SiteConfiguration | OK | Singleton (pk=1), get_config(). AI, Telegram (legacy), messaging templates. |
| TelegramUser | OK | telegram_user_id unique, indexed; last_seen indexed; phone/email optional; verified flags. |
| VerificationCode | OK | FK to TelegramUser; code_hashed; expires_at, used indexed. |
| AdRequest | OK | uuid unique indexed; status, created_at, (category, status) indexed; bot, user FKs SET_NULL; contact_snapshot JSON. |
| TelegramBot | OK | status, ordering; token encrypted via encryption.py. |
| TelegramSession | OK | (telegram_user_id, bot) unique; state, last_activity indexed; context JSON. |
| TelegramMessageLog | OK | bot + telegram_user_id + created_at index. |
| Indexes / constraints | OK | Indexes on status, created_at, FKs, unique where needed. |
| Nullability / defaults | OK | Sensible null=True for optional FKs and legacy fields. |
| Status flow | OK | pending_ai → pending_manual → approved/rejected (and expired/solved). |
| JSONFields | OK | contact_snapshot (AdRequest), context (TelegramSession), raw_payload (TelegramMessageLog), raw_telegram_json (AdRequest). Defaults dict where needed. |
| Migrations | OK | 0001–0007; data migration 0007 links AdRequest to TelegramUser where possible. |

### Migration notes

- All migrations are reversible where applicable.
- No manual SQL; backward-compatible defaults.
- SQLite: no DB-specific features used; portable.

---

## PART 3 — Views & URL Routing

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| Separation of concerns | OK | Views parse request, call services, return response. Business logic in services. |
| URL naming | OK | landing, dashboard, ad_list, ad_detail, settings, bot_*, api/*, telegram_webhook. |
| Auth / permission | OK | LoginRequiredMiddleware for all non-public URLs. @staff_member_required on dashboard, ad_*, settings, api/approve, api/reject, api/bulk-*, api/pulse, export/import, bot_*. |
| Public endpoints | Documented | `/`, `/login/`, `/logout/`, `/api/submit/`, `/telegram/webhook/<id>/`. |
| CSRF | OK | CSRF enabled; submit_ad and telegram_webhook are @csrf_exempt by design. |
| Staff-only | OK | All internal pages and internal APIs require staff. |

### Refactors applied

- `dashboard`: uses `get_dashboard_context()`.
- `api_pulse`: uses `get_pulse_data()`.
- `approve_ad`, `reject_ad`, `bulk_approve`, `bulk_reject`: use `approve_one_ad()` / `reject_one_ad()` and `parse_request_json()`.
- `import_config`, `bot_regenerate_webhook`, `submit_ad`: use `parse_request_json()` where applicable.

---

## PART 4 — Services Layer

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| core/services/ (package) | OK | ai_moderation: clean_ad_text, run_ai_moderation, test_openai_connection. telegram: send_telegram_message*, test_telegram_connection, get_webhook_info, set_webhook, delete_webhook. |
| Reusable / decoupled | OK | Services take config/bot/content; no request objects. |
| Error handling | OK | AI and Telegram failures caught; safe defaults (e.g. AI returns (True, '') on failure). |
| Logging | OK | logger.exception in AI and Telegram send; no secrets in logs (no token/key in messages). |
| Timeouts / retries | OK | Telegram send: timeout=10, max_retries=2 with backoff. |
| core/services/dashboard.py | NEW | get_pulse_data(), get_dashboard_context(). |
| core/services/ad_actions.py | NEW | approve_one_ad(ad, edited_content=None), reject_one_ad(ad, reason). |
| core/services/submit_ad_service.py | OK | SubmitAdService.submit(); transaction.atomic; uses clean_ad_text, run_ai_moderation. |
| core/services/users.py | OK | get_or_create_user_from_update, update_contact_info, validate_phone, validate_email. |
| core/services/otp.py | OK | generate_code, verify_code, hash_code; behind ENABLE_OTP. |
| core/services/conversation.py | OK | ConversationEngine; state machine; uses i18n, SubmitAdService. |

---

## PART 5 — API Endpoints

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| POST /api/submit/ | OK | Public, CSRF exempt. Returns 400 if no content, 500 on error, 200 + status/created. |
| POST /api/approve/ | OK | Staff. 400 missing ad_id / invalid state, 500 on exception. JSON: ad_id, optional content. |
| POST /api/reject/ | OK | Staff. 400 missing ad_id/reason / invalid state. JSON: ad_id, reason. |
| POST /api/bulk-approve/ | OK | Staff. ad_ids list (capped 50). |
| POST /api/bulk-reject/ | OK | Staff. ad_ids + reason. |
| GET /api/pulse/ | OK | Staff. JSON: total, pending_ai, pending_manual, approved_today, rejected_today, rejection_rate, pulse_score, system_health. |
| HTTP status codes | OK | 200 success, 400 validation, 403/404/429 where used, 500 server error. |
| JSON consistency | OK | success: { status, ... }; error: { status: 'error', message } or { error }. |
| Anonymous access | Documented | Only /api/submit/ and /telegram/webhook/*. |
| Future improvements | Noted | Optional API key/JWT for submit; rate limiting; abuse prevention. |

---

## PART 6 — AI Moderation

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| run_ai_moderation | OK | In core/services/ai_moderation.py; takes content + config. |
| JSON validation | OK | Regex extract + json.loads; approved/reason with defaults. |
| Exceptions | OK | try/except; returns (True, '') on failure. |
| Safe default | OK | AI never deletes; failure → treat as approved for flow. |
| Decoupled | OK | No view logic; used by submit_ad (view) and SubmitAdService. |
| Logging | OK | logger.exception('AI moderation failed: %s', e) — no API key in log. |

---

## PART 7 — Telegram Integration

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| Send functions | OK | send_telegram_message (config), send_telegram_message_via_bot (bot); _send_telegram_message_impl with retries. |
| Rejection + button | OK | send_telegram_rejection_with_button(_via_bot); resubmit link from username. |
| Message templates | OK | In SiteConfiguration; approval_message_template, rejection_message_template, submission_ack_message. |
| Failures logged | OK | logger.warning/exception in _send_telegram_message_impl. |
| Data mapping | OK | AdRequest.telegram_user_id, ad.uuid, ad.bot for notify. |
| Multi-bot | OK | TelegramBot model; send via bot when ad.bot is set and active. |
| Webhook | OK | telegram_views.telegram_webhook; secret validation; rate limit; conversation engine. |

---

## PART 8 — Authentication & Security

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| Internal views require login | OK | LoginRequiredMiddleware; public paths: /, /login/, /logout/, /api/submit/, /telegram/webhook/. |
| Staff-only | OK | @staff_member_required on dashboard, requests, settings, bots, api/approve, api/reject, api/pulse, export/import. |
| Security headers | OK | When DEBUG=False: SECURE_BROWSER_XSS_FILTER, SECURE_CONTENT_TYPE_NOSNIFF, X_FRAME_OPTIONS=DENY. |
| CSRF | OK | Enabled; submit_ad and telegram_webhook exempt by design. |
| Public /api/submit/ | Documented | No auth; consider firewall or optional token in production. |
| Sensitive config in DB | OK | API keys, bot tokens in SiteConfiguration / TelegramBot; tokens encrypted (TelegramBot), masked in UI. |
| Masking | OK | Bot token masked; TelegramUser phone/email masked in admin list. |

---

## PART 9 — Logging & Error Handling

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| Exceptions logged | OK | AI: logger.exception; Telegram: logger.warning/exception; SubmitAdService: logger.info. |
| No secrets in logs | OK | No API key or token in log messages. |
| AI/Telegram failures caught | OK | Handled in services; safe defaults. |
| Service return consistency | OK | AI: (bool, str); Telegram send: bool; pulse: dict. |
| Optional monitoring | Noted | Sentry/external logging can be added later. |

---

## PART 10 — Admin & Staff Interface

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| admin.py | OK | SiteConfiguration, AdRequest, TelegramBot, TelegramSession, TelegramMessageLog, TelegramUser, VerificationCode. |
| Inlines | OK | AdRequestInline on TelegramUser (read-only). |
| Filters | OK | Status, category, bot, state, direction, phone_verified, email_verified, ActiveUserFilter. |
| Search | OK | Set on models (content, uuid, telegram_user_id, etc.). |
| Sensitive data masked | OK | TelegramUser: masked_phone, masked_email. TelegramBot: bot_token_encrypted excluded. |
| VerificationCode | OK | Read-only; no add/change permission. |

---

## PART 11 — Testing & Quality

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| Existing tests | OK | test_auth_access, test_conversation, test_users_and_otp. |
| Models | Partial | Covered indirectly via conversation and users tests. No dedicated model unit tests (e.g. status transitions, constraints). |
| Views | Partial | test_auth_access: anonymous/staff/non-staff access and redirects. No direct tests for approve/reject response shapes. |
| Services | Partial | test_conversation (ConversationEngine, SubmitAdService); test_users_and_otp (users, otp). |
| Status flows | Partial | Conversation flow and submit path tested. |
| Missing coverage | Noted | run_ai_moderation (mocked OpenAI); send_telegram_* (mocked requests); dashboard get_pulse_data; ad_actions approve_one_ad/reject_one_ad; submit_ad view. |
| Migrations on fresh DB | OK | Standard Django migrations; no known dependency issues. |

---

## PART 12 — Documentation & Maintainability

### Checklist

| Item | Status | Notes |
|------|--------|--------|
| README | OK | Overview, setup, models, flow, URLs, API, AI, Telegram, settings, security. |
| Docstrings | OK | Models, services, and key view functions have docstrings. |
| Comments | OK | "Why" comments where needed (e.g. singleton, safe default). |
| BACKEND_AUDIT_REPORT.md | NEW | This document. |

---

## PART 13 — Summary Checklist

### Models audited

- **SiteConfiguration**: Singleton; AI, Telegram (legacy), messaging. OK.
- **TelegramUser**: Unique telegram_user_id; contact fields; verified flags; last_seen. OK.
- **VerificationCode**: Hashed code; channel; expires_at; used. OK.
- **AdRequest**: Status flow; bot/user FKs; contact_snapshot; indexes. OK.
- **TelegramBot**: Encrypted token; status; webhook. OK.
- **TelegramSession**: State machine; context JSON; unique (telegram_user_id, bot). OK.
- **TelegramMessageLog**: Audit log. OK.

### Views audited

- **landing**: Redirects authenticated to dashboard; renders minimal gateway. OK.
- **dashboard**: Uses get_dashboard_context(). OK.
- **api_pulse**: Uses get_pulse_data(). OK.
- **ad_list, ad_detail**: Staff; read-only presentation. OK.
- **settings_view, settings_save**: Staff; config save. OK.
- **test_telegram, test_openai**: Staff; no secrets saved. OK.
- **approve_ad, reject_ad, bulk_approve, bulk_reject**: Use ad_actions + parse_request_json. OK.
- **export_config, import_config**: Staff; no secrets in export. OK.
- **submit_ad**: Public; uses parse_request_json; creates AdRequest + AI. OK.
- **bot_***: Staff; CRUD and test/webhook. OK.
- **telegram_webhook**: CSRF exempt; secret; rate limit; conversation. OK.

### Services audited

- **core/services/ai_moderation.py**: clean_ad_text, run_ai_moderation, test_openai_connection. OK.
- **core/services/telegram.py**: send_telegram_message*, webhook helpers. OK.
- **core/services/dashboard.py**: get_pulse_data, get_dashboard_context. OK.
- **core/services/ad_actions.py**: approve_one_ad, reject_one_ad. NEW.
- **core/services/submit_ad_service.py**: SubmitAdService.submit. OK.
- **core/services/users.py**: get_or_create_user_from_update, update_contact_info, validation. OK.
- **core/services/otp.py**: generate_code, verify_code, hash_code; ENABLE_OTP. OK.
- **core/services/conversation.py**: ConversationEngine. OK.

### APIs audited

- **/api/submit/**: Public; POST; JSON/form; 200/400/500. OK.
- **/api/approve/**, **/api/reject/**: Staff; JSON; 200/400/500. OK.
- **/api/bulk-approve/**, **/api/bulk-reject/**: Staff; 200/400/500. OK.
- **/api/pulse/**: Staff; GET; 200 JSON. OK.

### Admin reviewed

- All models registered; inlines/filters/search; TelegramUser/VerificationCode masking and read-only where needed. OK.

### Security issues found

- **None critical.** Public /api/submit/ and /telegram/webhook/* are intentional; consider optional auth or rate limiting for submit in production.

### Logging issues

- **None.** No secrets in log messages. Exception messages could theoretically contain server-side details; consider sanitizing in high-security environments.

### Missing tests

- Unit tests for get_pulse_data / get_dashboard_context.
- Unit tests for approve_one_ad / reject_one_ad (with mocked Telegram).
- Optional: run_ai_moderation with mocked OpenAI; send_telegram_* with mocked requests.

### Migration notes

- 0001–0007 present and applied.
- 0007 data migration: links existing AdRequests to TelegramUser by telegram_user_id where a matching user exists.
- No manual SQL; backward-compatible defaults.

---

## Refactors Applied (This Audit)

1. **core/services/dashboard.py**: `get_pulse_data()`, `get_dashboard_context()` — single source for dashboard and api_pulse.
2. **core/services/ad_actions.py**: `approve_one_ad()`, `reject_one_ad()` — shared logic for single and bulk approve/reject.
3. **core/view_utils.py**: `parse_request_json(request)` — central JSON body parsing for views.
4. **core/views.py**: Dashboard and api_pulse use dashboard service; approve/reject/bulk use ad_actions and parse_request_json; removed duplicate logic and redundant json import.

Result: **Views are thinner, business logic is in services, and the backend is ready for phased production hardening and additional tests.**
