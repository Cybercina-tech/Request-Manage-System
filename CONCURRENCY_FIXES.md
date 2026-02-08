# Concurrency & Timeout Fixes

This document explains the root causes of request blocking/timeouts and the fixes applied for cPanel + Passenger (mod_passenger + Apache) deployment.

---

## Root Causes

### 1. SQLite file-level locking
- **Cause:** SQLite uses a single writer lock. When one request holds a transaction (e.g. during approval/delivery), others wait. Multiple tabs or concurrent requests serialize.
- **Symptom:** Second request times out while first completes.

### 2. Auto bot runner in request process
- **Cause:** `ENABLE_AUTO_BOTS` started a background thread that ran DB queries and Telegram HTTP calls every 10 seconds. This competed for the same SQLite connection and file lock as web requests.
- **Symptom:** Periodic stalls when the bot runner held the lock.

### 3. Blocking HTTP with long timeouts and sleep
- **Cause:** Instagram (30s timeout, 2s sleep × 3 retries) and Telegram (30s read, 0.5s+ backoff) could block the request thread for many seconds. OpenAI had no explicit timeout.
- **Symptom:** Approval, webhook, or API submit requests hang when external services are slow.

### 4. SiteConfiguration DB hit on every request
- **Cause:** `site_config` context processor called `SiteConfiguration.get_config()` (DB) on every template render.
- **Symptom:** Extra SQLite lock contention on every page load.

---

## Fixes Applied

### 1. SQLite (settings.py, core/apps.py)
- **OPTIONS['timeout'] = 15** — Wait up to 15 seconds for lock before raising `OperationalError` instead of hanging indefinitely.
- **PRAGMA journal_mode=WAL** — Enables concurrent reads during writes; reduces blocking.
- **PRAGMA busy_timeout=15000** — Same as `timeout` (ms) for explicit busy wait.
- **CONN_MAX_AGE = 0** — Release connection after each request; avoids long-lived locks across requests.

**Why it helps:** Faster failure on lock contention; WAL allows readers to proceed during writes.

### 2. Disable auto bot runner under Passenger (bot_runner.py, passenger_wsgi.py)
- Skip auto-start when `PASSENGER_APP_ENV` or `Phusion_Passenger` in `SERVER_SOFTWARE` is present.
- `passenger_wsgi.py` sets `PASSENGER_APP_ENV=production`.

**Why it helps:** No background thread competing for DB/CPU in the web process. Run bots separately via cron:
```bash
* * * * * cd /path/to/project && python manage.py runbots
```

### 3. Stricter HTTP timeouts and shorter retries
- **Instagram:** `REQUEST_TIMEOUT=15`, `MAX_RETRIES=2`, `RETRY_DELAY_SECONDS=0.5`
- **Telegram:** `DEFAULT_CONNECT_TIMEOUT=5`, `DEFAULT_READ_TIMEOUT=15`, `MAX_RETRIES=2`, `BACKOFF_FACTOR=0.3`
- **OpenAI:** `timeout=15.0` (or 10.0 for test) on client.

**Why it helps:** Fail fast; less time blocking the request thread.

### 4. Cache SiteConfiguration (context_processors.py, models.py)
- Cache `SiteConfiguration.get_config()` for 60 seconds.
- Invalidate cache on `SiteConfiguration.save()`.

**Why it helps:** One DB hit per minute per process instead of every request.

### 5. CACHES (settings.py)
- Explicit `LocMemCache` for rate limiting and SiteConfig cache.
- Avoids implicit cache behavior and ensures consistent usage.

---

## Verification Checklist

- [ ] **Set ENABLE_AUTO_BOTS=false** in cPanel env (or rely on `PASSENGER_APP_ENV` auto-detect)
- [ ] **Run bots separately** if using polling: `python manage.py runbots` via cron
- [ ] **Confirm WAL mode:** After deploy, run `PRAGMA journal_mode;` on db.sqlite3 — should return `wal`
- [ ] **Test two tabs:** Open dashboard in two tabs; both should load without timeout
- [ ] **Test concurrent API:** Two simultaneous `/api/v1/list/` requests; both should succeed
- [ ] **Check Passenger workers:** In cPanel, ensure Passenger is configured for multiple app processes if available
- [ ] **Monitor logs:** No `OperationalError: database is locked` or similar

---

## Recommended: Migrate to PostgreSQL

For production with concurrent traffic, **PostgreSQL** eliminates SQLite’s file-level locking:

1. Create MySQL/PostgreSQL DB in cPanel (if PostgreSQL is available) or use external DB.
2. Update `DATABASES` in settings; remove SQLite-specific `OPTIONS` and `connection_created` pragmas.
3. Remove `_setup_sqlite_pragmas` from `core/apps.py` when no longer using SQLite.

---

## Files Changed

| File | Change |
|------|--------|
| `iraniu/settings.py` | SQLite timeout, CONN_MAX_AGE, CACHES |
| `core/apps.py` | connection_created → WAL, busy_timeout |
| `core/services/bot_runner.py` | Skip auto-bots under Passenger |
| `core/services/instagram.py` | Shorter timeout, fewer retries, less sleep |
| `core/services/telegram_client.py` | Shorter timeouts, fewer retries, shorter backoff |
| `core/services/ai_moderation.py` | OpenAI client timeout |
| `core/context_processors.py` | Cache SiteConfiguration |
| `core/models.py` | Invalidate cache on SiteConfiguration.save |
| `passenger_wsgi.py` | Set PASSENGER_APP_ENV |
