import os
import sys
import logging
import asyncio
from pathlib import Path

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import psutil

# Гарантуємо, що папка проєкту знаходиться в sys.path для уникнення помилок імпорту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Прямі та чисті імпорти локальних модулів без крапок
from database import (
    init_db, get_user, register_user, is_login_taken, 
    get_all_users, update_user_status, add_chat_message, get_chat_history
)
from gemma import ask_gemma

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("system_monitor")

# Load .env file manually
def load_env():
    env_path = Path(".env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, val = line.split("=", 1)
                os.environ.setdefault(key.strip(), val.strip())

load_env()

# Retrieve Configuration
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = os.getenv("ADMIN_TELEGRAM_ID")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://192.168.56.1:11434")

if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
    logger.error("TELEGRAM_BOT_TOKEN is not set. Please update your .env file.")
    sys.exit("Error: TELEGRAM_BOT_TOKEN is not configured.")

if not ADMIN_ID:
    logger.error("ADMIN_TELEGRAM_ID is not set in .env!")
else:
    ADMIN_ID = int(ADMIN_ID)

DISK_PATH = os.getenv("MONITOR_DISK_PATH", "/")
logger.info(f"System Monitor Bot starting. Target disk path: '{DISK_PATH}'")

# Initialize Bot and Dispatcher
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Ініціалізація станів FSM
class RegisterStates(StatesGroup):
    waiting_for_login = State()
    waiting_for_password = State()

class GemmaStates(StatesGroup):
    chatting = State()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ МОНИТОРИНГА ---
def make_progress_bar(percent: float, length: int = 10) -> str:
    filled_length = int(round(length * percent / 100))
    bar = '█' * filled_length + '░' * (length - filled_length)
    return f"<code>[{bar}]</code> <b>{percent:.1f}%</b>"

def get_size_format(b: float, factor: int = 1024) -> str:
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if b < factor:
            return f"{b:.2f} {unit}"
        b /= factor
    return f"{b:.2f} YB"

async def fetch_system_metrics():
    cpu_usage = await asyncio.to_thread(psutil.cpu_percent, interval=1.0)
    mem = await asyncio.to_thread(psutil.virtual_memory)
    try:
        disk = await asyncio.to_thread(psutil.disk_usage, DISK_PATH)
        disk_info = {
            "total": get_size_format(disk.total),
            "used": get_size_format(disk.used),
            "free": get_size_format(disk.free),
            "percent": disk.percent,
            "error": None
        }
    except Exception as e:
        logger.error(f"Error reading disk usage for path '{DISK_PATH}': {e}")
        disk_info = {"total": "N/A", "used": "N/A", "free": "N/A", "percent": 0.0, "error": str(e)}
    return cpu_usage, mem, disk_info

# --- МИДЛВАРЬ / ПРОВЕРКА ДЕЙСТВИЙ ---
async def check