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
    
    conn.commit()
    conn.close()
    logger.info("Database initialized successfully.")

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

def is_login_taken(login: str) -> bool:
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM users WHERE login = ?", (login,))
    row = cursor.fetchone()
    conn.close()
    return row is not None
