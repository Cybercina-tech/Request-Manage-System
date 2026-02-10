# Clean Slate (DB Reset) and Telegram Token Storage

## When to use

- **Database disk image is malformed** (SQLite corruption)
- You want a fresh database and to re-run all migrations
- You need to create a new superuser after reset

---

## Clean slate — Windows (PowerShell)

Run from the **project root** (`Request-Manage-System-1`). Use this exact sequence for a clean slate:

```powershell
# 1. Stop the Django server and any runbots process (Ctrl+C in those terminals).

# 2. Reset SQLite (removes db + WAL files, then runs migrate)
.\scripts\reset_sqlite_db.ps1

# 3. If the script fails at migrate (e.g. unapplied migration core.0021), run migrate explicitly:
python manage.py migrate

# 4. Create a new superuser so you can log in again
python manage.py createsuperuser
```

### Manual commands (if you prefer not to use the script)

```powershell
# From project root
Remove-Item -Force db.sqlite3 -ErrorAction SilentlyContinue
Remove-Item -Force db.sqlite3-shm -ErrorAction SilentlyContinue
Remove-Item -Force db.sqlite3-wal -ErrorAction SilentlyContinue
python manage.py migrate
python manage.py createsuperuser
```

---

## Where Telegram bot tokens are stored

- **Model:** `core.TelegramBot`
- **Field:** `bot_token_encrypted` (encrypted at rest; never log or expose in templates)
- **Default bot:** The row with `is_default=True` (only one). Used by the system when a single bot is expected.
- **Where to update:**
  1. **Bots page (staff):** `/bots/` → Edit a bot → set **Bot token** and save.
  2. **Default bot token (API):** POST to `/bots/<id>/update-token/` with `bot_token` (form or JSON).
  3. **Django admin:** Admin → Core → Telegram bots → edit and save.

After updating a token, the bot’s `last_error` is cleared on the next successful `getMe`. Invalid tokens are stored as `last_error` and status `ERROR`; 401/Unauthorized is logged at DEBUG level to avoid log flooding.

---

## After reset

1. Log in with the new superuser.
2. Re-create or edit **Telegram bots** (Bots page) and set valid tokens.
3. Ensure one bot has **Is default** checked if you use the default-bot flow.
4. Run the server: `python manage.py runserver` (or gunicorn in production).
