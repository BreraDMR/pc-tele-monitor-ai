import sqlite3
import os
import hashlib
import logging

# Шлях до файлу БД. У Docker монтуємо каталог (напр. /app/data) і вказуємо
# DB_PATH=/app/data/system_monitor_bot.db, щоб bind-mount не створював директорію
# замість файлу. Для локального запуску лишається файл у поточній теці.
DB_PATH = os.getenv("DB_PATH", "system_monitor_bot.db")

logger = logging.getLogger("system_monitor.db")

def get_db_connection():
    # Гарантуємо, що каталог для файлу БД існує
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # Create users table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        login TEXT UNIQUE,
        password_hash TEXT,
        status TEXT DEFAULT 'pending_approval', -- 'pending_approval', 'approved', 'rejected', 'banned'
        can_use_status INTEGER DEFAULT 1,
        can_use_gemma INTEGER DEFAULT 1,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    # Create chat history table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS chat_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        role TEXT, -- 'user', 'assistant'
        message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
    );
    """)

    # Create token usage table (per AI request, prompt+completion tokens)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS token_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER,
        prompt_tokens INTEGER,
        completion_tokens INTEGER,
        total_tokens INTEGER,
        is_estimated INTEGER DEFAULT 0, -- 1 если посчитано приблизительно (модель не вернула usage)
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
    );
    """)

    # Create audit log table (admin actions: approve/reject/ban/unban)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_telegram_id INTEGER,
        admin_label TEXT, -- снэпшот @username/имени админа на момент действия
        action TEXT,
        target_telegram_id INTEGER,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Per-user AI preferences chosen via /model: which model tier to chat with
    # ('weak' qwen2.5:3b / 'strong' qwen2.5:14b / 'very_strong' qwen3:30b-a3b)
    # and ai_memory -- when on (1) the model is kept resident in Ollama for an
    # hour after use instead of the usual few minutes.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS user_settings (
        telegram_id INTEGER PRIMARY KEY,
        ai_model TEXT NOT NULL DEFAULT 'weak', -- weak | strong | very_strong
        ai_memory INTEGER NOT NULL DEFAULT 0,  -- 0 = unload after ~5m, 1 = keep 1h
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    # Migration: add ai_memory to user_settings tables created before it existed.
    existing_cols = {row["name"] for row in cursor.execute("PRAGMA table_info(user_settings)")}
    if "ai_memory" not in existing_cols:
        cursor.execute("ALTER TABLE user_settings ADD COLUMN ai_memory INTEGER NOT NULL DEFAULT 0")

    conn.commit()
    conn.close()
    logger.info("Database initialized successfully.")


# The AI model tiers a user can pick via /model. Keep in sync with the
# CHAT_MODEL* env vars resolved in monitor.py.
MODEL_TIERS = ("weak", "strong", "very_strong")


def get_model_pref(telegram_id: int) -> str:
    """Returns a tier from MODEL_TIERS -- defaults to 'weak' for a first-time user."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ai_model FROM user_settings WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()
    return row["ai_model"] if row and row["ai_model"] in MODEL_TIERS else "weak"


def set_model_pref(telegram_id: int, model: str) -> None:
    if model not in MODEL_TIERS:
        return
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_settings (telegram_id, ai_model) VALUES (?, ?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET ai_model = excluded.ai_model, updated_at = CURRENT_TIMESTAMP",
        (telegram_id, model),
    )
    conn.commit()
    conn.close()


def get_memory_pref(telegram_id: int) -> bool:
    """Whether the user enabled 'memory' (keep the model resident for an hour)."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT ai_memory FROM user_settings WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()
    return bool(row["ai_memory"]) if row else False


def set_memory_pref(telegram_id: int, enabled: bool) -> None:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO user_settings (telegram_id, ai_memory) VALUES (?, ?) "
        "ON CONFLICT(telegram_id) DO UPDATE SET ai_memory = excluded.ai_memory, updated_at = CURRENT_TIMESTAMP",
        (telegram_id, 1 if enabled else 0),
    )
    conn.commit()
    conn.close()

def hash_password(password: str) -> str:
    salt = os.urandom(16) # Generate a random salt
    pwd_hash = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return salt.hex() + ":" + pwd_hash.hex()

def register_user(telegram_id: int, username: str, login: str, password_plain: str) -> bool:
    pwd_hash = hash_password(password_plain)
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO users (telegram_id, username, login, password_hash, status, can_use_status, can_use_gemma) VALUES (?, ?, ?, ?, 'pending_approval', 1, 1)",
            (telegram_id, username, login, pwd_hash)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_user(telegram_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    row = cursor.fetchone()
    conn.close()
    return row

def get_all_users():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users ORDER BY created_at DESC")
    rows = cursor.fetchall()
    conn.close()
    return rows

def update_user_status(telegram_id: int, status: str, can_use_status: int = None, can_use_gemma: int = None):
    conn = get_db_connection()
    cursor = conn.cursor()
    
    if can_use_status is not None and can_use_gemma is not None:
        cursor.execute(
            "UPDATE users SET status = ?, can_use_status = ?, can_use_gemma = ? WHERE telegram_id = ?",
            (status, can_use_status, can_use_gemma, telegram_id)
        )
    elif can_use_status is not None:
        cursor.execute(
            "UPDATE users SET status = ?, can_use_status = ? WHERE telegram_id = ?",
            (status, can_use_status, telegram_id)
        )
    elif can_use_gemma is not None:
        cursor.execute(
            "UPDATE users SET status = ?, can_use_gemma = ? WHERE telegram_id = ?",
            (status, can_use_gemma, telegram_id)
        )
    else:
        cursor.execute(
            "UPDATE users SET status = ? WHERE telegram_id = ?",
            (status, telegram_id)
        )
        
    conn.commit()
    conn.close()

def update_permissions(telegram_id: int, can_use_status: int, can_use_gemma: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE users SET can_use_status = ?, can_use_gemma = ? WHERE telegram_id = ?",
        (can_use_status, can_use_gemma, telegram_id)
    )
    conn.commit()
    conn.close()

def add_chat_message(telegram_id: int, role: str, message: str):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO chat_history (telegram_id, role, message) VALUES (?, ?, ?)",
        (telegram_id, role, message)
    )
    conn.commit()
    conn.close()

def get_chat_history(telegram_id: int, limit: int = 20):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, message FROM chat_history WHERE telegram_id = ? ORDER BY id DESC LIMIT ?",
        (telegram_id, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    # Reverse to get chronological order
    return list(reversed(rows))

def clear_chat_history(telegram_id: int):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM chat_history WHERE telegram_id = ?", (telegram_id,))
    conn.commit()
    conn.close()

def add_token_usage(telegram_id: int, prompt_tokens: int, completion_tokens: int, is_estimated: bool = False):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO token_usage (telegram_id, prompt_tokens, completion_tokens, total_tokens, is_estimated) "
        "VALUES (?, ?, ?, ?, ?)",
        (telegram_id, prompt_tokens, completion_tokens, prompt_tokens + completion_tokens, int(is_estimated))
    )
    conn.commit()
    conn.close()

def get_token_usage_per_user():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            u.telegram_id AS telegram_id,
            u.login AS login,
            COUNT(t.id) AS requests,
            COALESCE(SUM(t.prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(t.completion_tokens), 0) AS completion_tokens,
            COALESCE(SUM(t.total_tokens), 0) AS total_tokens,
            MAX(t.is_estimated) AS has_estimated
        FROM users u
        JOIN token_usage t ON t.telegram_id = u.telegram_id
        GROUP BY u.telegram_id
        ORDER BY total_tokens DESC
    """)
    rows = cursor.fetchall()
    conn.close()
    return rows

def get_total_token_usage():
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            COUNT(id) AS requests,
            COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens,
            COALESCE(SUM(completion_tokens), 0) AS completion_tokens,
            COALESCE(SUM(total_tokens), 0) AS total_tokens
        FROM token_usage
    """)
    row = cursor.fetchone()
    conn.close()
    return row

def add_audit_log(admin_telegram_id: int, admin_label: str, action: str, target_telegram_id: int = None, details: str = ""):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO audit_log (admin_telegram_id, admin_label, action, target_telegram_id, details) "
        "VALUES (?, ?, ?, ?, ?)",
        (admin_telegram_id, admin_label, action, target_telegram_id, details)
    )
    conn.commit()
    conn.close()

def get_audit_log(limit: int = 20):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            a.admin_label AS admin_label,
            a.action AS action,
            a.target_telegram_id AS target_telegram_id,
            u.login AS target_login,
            a.details AS details,
            a.created_at AS created_at
        FROM audit_log a
        LEFT JOIN users u ON u.telegram_id = a.target_telegram_id
        ORDER BY a.id DESC
        LIMIT ?
    """, (limit,))
    rows = cursor.fetchall()
    conn.close()
    return rows

def is_login_taken(login: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users WHERE login = ?", (login,))
    row = cursor.fetchone()
    conn.close()
    return row is not None
