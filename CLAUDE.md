# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A Telegram bot (aiogram 3, async) that lets approved users check a PC's CPU/RAM/disk (`psutil`) and chat with a local LLM (Gemma 2 9B via Ollama's OpenAI-compatible API) including voice input. SQLite stores users, chat history, token usage and an admin audit log. Runs in Docker on a Windows host; Ollama runs directly on that host (not containerized), reached over the LAN.

## Commands

- Run locally: `pip install -r requirements.txt && python monitor.py` (needs a `.env`, see `.env.example`; at minimum `TELEGRAM_BOT_TOKEN`).
- Run in Docker: `docker-compose up --build -d`; logs with `docker-compose logs -f --tail=50`.
- Syntax/import check (there is no test suite): `python3 -m py_compile monitor.py database.py gemma.py voice.py`.
- `bot_update.bat` is the deploy script that runs *on the Windows host* (hard-resets the repo to `origin/main`, rebuilds and restarts the container). Not meant to be run from inside this sandbox.

## Architecture

Four files, one concern each, imported flat (no package, no relative imports — `sys.path.insert` shim at the top of `monitor.py` exists for the Docker container's working dir):

- `monitor.py` — aiogram handlers, FSM states, and the bot entrypoint (`main()`). All Telegram-facing logic lives here.
- `database.py` — the only module touching SQLite. Plain `sqlite3` (no ORM), every function opens/closes its own connection. All writes are synchronous and called directly from async handlers (not wrapped in `asyncio.to_thread`) — acceptable at this scale, worth keeping in mind if load ever grows.
- `gemma.py` — `ask_gemma()` is the single entry point to the LLM. It owns the side effects: reads/writes `chat_history` and writes `token_usage` itself, so callers in `monitor.py` never duplicate persistence logic. Takes a shared `aiohttp.ClientSession` (created once in `main()`, not per-request).
- `voice.py` — wraps `faster-whisper`. Model is loaded lazily (module-level singleton) on first voice message, not at startup, so the bot doesn't pay the load cost unless voice is actually used.

### Access control
Admins are **not** rows in the `users` table — they're a set of Telegram IDs from env (`ADMIN_TELEGRAM_IDS`, comma-separated; falls back to the legacy single `ADMIN_TELEGRAM_ID` if unset). `is_admin()` in `monitor.py` is the only check; `check_user_access()` short-circuits to a synthetic full-access dict for admins before ever touching the DB.

Regular users go through an FSM registration (`RegisterStates`: login → password, PBKDF2-hashed) into `pending_approval`, then an admin approves/rejects via inline buttons. Approved users have two independent permission flags on their `users` row (`can_use_status`, `can_use_gemma`) that admins can toggle — these gate `/status` and `/gemma` (+ `/history`, `/forget`, voice input) separately.

### Gemma chat + voice
`GemmaStates.chatting` is the FSM state both the text handler (`F.text`) and voice handler (`F.voice`) are scoped to — voice notes are only transcribed while the user is inside `/gemma` mode, same constraint as text. Both funnel into `_reply_with_gemma()` so the actual "call the model, chunk the reply for Telegram's 4096-char limit" logic exists once. Voice messages are saved to a temp `.ogg`, transcribed (`asyncio.to_thread`, since faster-whisper is blocking), echoed back to the user as `🎤 Распознано: ...` for transparency, then treated like a normal text turn.

### Token accounting
`token_usage` rows come from Ollama's `usage` field in the OpenAI-compatible response when present (real counts from the model's own tokenizer); if absent, `gemma.py` falls back to a `len(text)//4` estimate and flags the row `is_estimated`. `/tokens` (admin-only) aggregates this per user.

### Command menu
Telegram's native "/" suggestion menu is per-chat, not global: `build_command_list()` is the single source of truth mapping (is_admin, can_use_status, can_use_gemma) → visible commands, applied via `bot.set_my_commands(scope=BotCommandScopeChat(...))`. It's re-applied whenever permissions change (approve/ban/unban) and on `/start`, since there's no other hook that fires on a permission change.

### Audit log
`audit_log` rows store a text snapshot of the acting admin (`@username` or full name) rather than an FK, because admins aren't necessarily present in `users`. Logged actions: `approve`, `reject`, `ban`, `unban`. `/auditlog` (admin-only) reads the last 20.

### Config gotchas worth knowing
- The bot's own `.env` loader (`load_env()` in `monitor.py`) is hand-rolled (`os.environ.setdefault`, no `python-dotenv`) — real environment variables always win over `.env` values.
- `OLLAMA_URL` is the canonical var name; `OLLAMA_API_URL` is accepted as a fallback because it crept into a real deployment's `.env` historically. Same pattern for `ADMIN_TELEGRAM_IDS`/`ADMIN_TELEGRAM_ID`.
- `DB_PATH` defaults to a local file for bare `python monitor.py` runs, but Docker Compose overrides it to `/app/data/system_monitor_bot.db` (a mounted *directory*, not a bind-mounted file — bind-mounting the file directly causes Docker to create a directory in its place on a fresh checkout).
- `PROCFS_PATH` is only applied if the path actually exists on disk (checked at startup) — otherwise `psutil` would crash trying to read a host `/proc` that isn't mounted.

### DB migration policy
This bot has a live deployment with real user data. `database.py`'s `init_db()` only ever does `CREATE TABLE IF NOT EXISTS` — never alter or drop existing tables/columns. Treat this the same way when adding new persisted fields (add a new table, or a new nullable column via a guarded `ALTER TABLE` that tolerates already having run).
