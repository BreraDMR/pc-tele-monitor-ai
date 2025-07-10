from __future__ import annotations

import os
import sys
import html
import logging
import asyncio
import tempfile
from pathlib import Path

import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton,
    BotCommand, BotCommandScopeDefault, BotCommandScopeChat
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

import psutil

# Гарантуємо, що папка проєкту знаходиться в sys.path для уникнення помилок імпорту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Прямі та чисті імпорти локальних модулів без крапок
from database import (
    init_db, get_user, register_user, is_login_taken,
    get_all_users, update_user_status, clear_chat_history, get_chat_history,
    get_token_usage_per_user, get_total_token_usage,
    add_audit_log, get_audit_log,
    get_model_pref, set_model_pref, get_memory_pref, set_memory_pref, MODEL_TIERS
)
from gemma import ask_gemma
from voice import transcribe_voice

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
# Підтримуємо список адмінів через ADMIN_TELEGRAM_IDS="123,456", а також стару
# одиночну ADMIN_TELEGRAM_ID — для зворотної сумісності, якщо нової змінної немає.
_admin_ids_raw = os.getenv("ADMIN_TELEGRAM_IDS") or os.getenv("ADMIN_TELEGRAM_ID") or ""
ADMIN_IDS = {int(x.strip()) for x in _admin_ids_raw.split(",") if x.strip()}
# Підтримуємо обидва імені змінної — в .env історично трапляється OLLAMA_API_URL,
# хоча .env.example і код орієнтовані на OLLAMA_URL.
OLLAMA_URL = os.getenv("OLLAMA_URL") or os.getenv("OLLAMA_API_URL") or "http://192.168.56.1:11434"

# Three AI model tiers a user can pick via /model. resolve_model() maps the
# stored 'weak'/'strong'/'very_strong' preference to the actual Ollama model.
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen2.5:3b")
CHAT_MODEL_STRONG = os.getenv("CHAT_MODEL_STRONG", "qwen2.5:14b")
CHAT_MODEL_VERY_STRONG = os.getenv("CHAT_MODEL_VERY_STRONG", "qwen3:30b-a3b")

MODEL_LABELS = {
    "weak": "Слабая (быстрая)",
    "strong": "Сильная (умнее)",
    "very_strong": "Очень сильная (самая умная)",
}
MODEL_NAMES = {
    "weak": CHAT_MODEL,
    "strong": CHAT_MODEL_STRONG,
    "very_strong": CHAT_MODEL_VERY_STRONG,
}

# How long Ollama keeps the chosen model resident after a request, depending on
# the per-user /model memory toggle: off unloads it soon (frees RAM for the
# other bots), on pins it for an hour so a back-and-forth chat stays warm.
KEEP_ALIVE_OFF = "5m"
KEEP_ALIVE_ON = "1h"


def resolve_model(user_id: int) -> str:
    """Map a user's tier preference to the actual Ollama model name."""
    return MODEL_NAMES.get(get_model_pref(user_id), CHAT_MODEL)


def resolve_keep_alive(user_id: int) -> str:
    """How long to keep the model resident, based on the user's memory toggle."""
    return KEEP_ALIVE_ON if get_memory_pref(user_id) else KEEP_ALIVE_OFF


if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
    logger.error("TELEGRAM_BOT_TOKEN is not set. Please update your .env file.")
    sys.exit("Error: TELEGRAM_BOT_TOKEN is not configured.")

if not ADMIN_IDS:
    logger.error("ADMIN_TELEGRAM_IDS (or legacy ADMIN_TELEGRAM_ID) is not set in .env!")

def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

DISK_PATH = os.getenv("MONITOR_DISK_PATH", "/")
logger.info(f"System Monitor Bot starting. Target disk path: '{DISK_PATH}'")

# Initialize Bot and Dispatcher
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

# Єдина aiohttp-сесія для всіх запитів до Ollama (створюється в main(), а не на кожен запит)
http_session: aiohttp.ClientSession | None = None

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

# --- "/"-МЕНЮ КОМАНД TELEGRAM ---
def build_command_list(is_admin: bool, can_use_status: int = 0, can_use_gemma: int = 0) -> list[BotCommand]:
    commands = [
        BotCommand(command="start", description="Приветствие и статус доступа"),
        BotCommand(command="help", description="Справка по командам"),
    ]
    if is_admin:
        commands += [
            BotCommand(command="status", description="Системный отчёт CPU/RAM/диск"),
            BotCommand(command="gemma", description="Чат с ИИ"),
            BotCommand(command="model", description="Модель ИИ и память"),
            BotCommand(command="history", description="Последние сообщения с ИИ"),
            BotCommand(command="forget", description="Очистить память диалога с ИИ"),
            BotCommand(command="admin", description="Панель управления пользователями"),
            BotCommand(command="tokens", description="Расход токенов по пользователям"),
            BotCommand(command="auditlog", description="Журнал действий админов"),
        ]
        return commands

    if can_use_status:
        commands.append(BotCommand(command="status", description="Системный отчёт CPU/RAM/диск"))
    if can_use_gemma:
        commands.append(BotCommand(command="gemma", description="Чат с ИИ"))
        commands.append(BotCommand(command="model", description="Модель ИИ и память"))
        commands.append(BotCommand(command="history", description="Последние сообщения с ИИ"))
        commands.append(BotCommand(command="forget", description="Очистить память диалога с ИИ"))
    if not can_use_status and not can_use_gemma:
        commands.append(BotCommand(command="register", description="Подать заявку на доступ"))
    return commands

async def apply_user_commands(telegram_id: int, is_admin: bool, can_use_status: int = 0, can_use_gemma: int = 0):
    try:
        await bot.set_my_commands(
            build_command_list(is_admin, can_use_status, can_use_gemma),
            scope=BotCommandScopeChat(chat_id=telegram_id)
        )
    except Exception:
        logger.exception(f"Failed to set command menu for {telegram_id}")

# --- МИДЛВАРЬ / ПРОВЕРКА ДЕЙСТВИЙ ---
async def check_user_access(message: Message) -> dict | None:
    if is_admin(message.from_user.id):
        return {"status": "approved", "can_use_status": 1, "can_use_gemma": 1}
        
    user = get_user(message.from_user.id)
    if not user or user["status"] != "approved":
        if not user:
            await message.reply("🔒 Для работы с ботом необходимо пройти регистрацию.\nИспользуйте команду /register")
        elif user["status"] == "pending_approval":
            await message.reply("⏳ Ваша заявка всё еще находится на рассмотрении у Администратора.")
        elif user["status"] in ["rejected", "banned"]:
            await message.reply("❌ Доступ к боту заблокирован или был отклонен.")
        return None
        
    return user

# --- ХЭНДЛЕРЫ ---

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    user = get_user(message.from_user.id)
    user_is_admin = is_admin(message.from_user.id)

    welcome_text = f"👋 <b>Welcome to the Secure System Monitor Bot!</b>\n\n"

    if user_is_admin:
        welcome_text += "👑 Вы являетесь администратором системы.\nДоступные команды:\n📊 /status - Просмотр метрик\n🛠️ /admin - Панель управления\n🧠 /gemma - Чат с AI\n📈 /tokens - Расход токенов\n📜 /auditlog - Журнал действий админов"
        await apply_user_commands(message.from_user.id, is_admin=True)
    elif user and user["status"] == "approved":
        welcome_text += "✅ Вы успешно авторизованы.\n\n<b>Доступные функции:</b>\n"
        if user["can_use_status"]: welcome_text += "📊 /status - Получить системный отчет\n"
        if user["can_use_gemma"]: welcome_text += "🧠 /gemma - Чат с AI\n🗂️ /history - История диалога\n🧹 /forget - Очистить память\n"
        await apply_user_commands(message.from_user.id, is_admin=False, can_use_status=user["can_use_status"], can_use_gemma=user["can_use_gemma"])
    else:
        welcome_text += "🔒 Доступ закрыт. Чтобы начать использование, пройдите регистрацию:\n📝 /register"
        await apply_user_commands(message.from_user.id, is_admin=False)

    welcome_text += "\n\nℹ️ /help — список всех команд"
    await message.reply(welcome_text)

@dp.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    user_is_admin = is_admin(message.from_user.id)
    user = get_user(message.from_user.id)

    text = "ℹ️ <b>Справка по командам</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    text += "▫️ /start — приветствие и статус доступа\n"
    text += "▫️ /help — эта справка\n"

    if user_is_admin:
        text += "📊 /status — системный отчёт (CPU/RAM/диск)\n"
        text += "🧠 /gemma — чат с ИИ (выход — /bye)\n"
        text += "🤖 /model — модель ИИ (слабая/сильная/очень сильная) + память\n"
        text += "🗂️ /history — последние сообщения с ИИ\n"
        text += "🧹 /forget — очистить память диалога с ИИ\n"
        text += "👑 /admin — панель управления пользователями\n"
        text += "📈 /tokens — расход токенов по пользователям\n"
        text += "📜 /auditlog — журнал действий админов\n"
    elif user and user["status"] == "approved":
        if user["can_use_status"]:
            text += "📊 /status — системный отчёт (CPU/RAM/диск)\n"
        if user["can_use_gemma"]:
            text += "🧠 /gemma — чат с ИИ (выход — /bye)\n"
            text += "🤖 /model — модель ИИ (слабая/сильная/очень сильная) + память\n"
            text += "🗂️ /history — последние сообщения с ИИ\n"
            text += "🧹 /forget — очистить память диалога с ИИ\n"
    else:
        text += "📝 /register — подать заявку на доступ\n"

    text += "\n<i>В режиме ИИ-чата напишите /bye, чтобы выйти. Также можно прислать голосовое сообщение — оно будет распознано и передано ИИ.</i>"
    await message.reply(text)

@dp.message(Command("register"))
async def start_registration(message: Message, state: FSMContext):
    if is_admin(message.from_user.id):
        return await message.reply("👑 Админу не нужно регистрироваться!")
        
    user = get_user(message.from_user.id)
    if user:
        return await message.reply(f"Вы уже зарегистрированы. Статус: <b>{user['status']}</b>")
        
    await state.set_state(RegisterStates.waiting_for_login)
    await message.reply("📝 Начнем регистрацию.\nПридумайте и отправьте желаемый <b>Логин</b>:")

@dp.message(RegisterStates.waiting_for_login, F.text)
async def process_login(message: Message, state: FSMContext):
    login = message.text.strip()
    if len(login) < 3:
        return await message.reply("❌ Логин должен содержать не менее 3 символов. Попробуйте еще раз:")
        
    if is_login_taken(login):
        return await message.reply("❌ Этот логин уже занят. Придумайте другой:")
        
    await state.update_data(chosen_login=login)
    await state.set_state(RegisterStates.waiting_for_password)
    await message.reply("🔑 Теперь придумайте и отправьте надежный <b>Пароль</b>:")

@dp.message(RegisterStates.waiting_for_password, F.text)
async def process_password(message: Message, state: FSMContext):
    password = message.text.strip()
    if len(password) < 4:
        return await message.reply("❌ Пароль слишком короткий. Придумайте более надежный:")
        
    data = await state.get_data()
    login = data['chosen_login']
    
    success = register_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username or message.from_user.full_name,
        login=login,
        password_plain=password
    )
    
    await state.clear()
    
    if success:
        await message.reply("🎉 Регистрация завершена! Ваша учетная запись отправлена Администратору на одобрение. Ожидайте.")
        
        if ADMIN_IDS:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{message.from_user.id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{message.from_user.id}")
                ]
            ])
            for admin_id in ADMIN_IDS:
                try:
                    await bot.send_message(
                        admin_id,
                        f"🔔 <b>Новая заявка на регистрацию!</b>\n\n"
                        f"👤 Пользователь: {html.escape(message.from_user.full_name)}\n"
                        f"🏷️ Username: @{html.escape(message.from_user.username or 'none')}\n"
                        f"🆔 ID: <code>{message.from_user.id}</code>\n"
                        f"📝 Выбранный логин: <code>{html.escape(login)}</code>",
                        reply_markup=kb
                    )
                except Exception:
                    logger.exception(f"Failed to notify admin {admin_id} about new registration")
    else:
        await message.reply("❌ Произошла ошибка при сохранении данных. Попробуйте снова через /register")

@dp.callback_query(F.data.startswith("approve_") | F.data.startswith("reject_"))
async def handle_approval_buttons(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return await callback.answer("У вас нет прав на это действие.", show_alert=True)

    action, target_id = callback.data.split("_")
    target_id = int(target_id)
    admin_label = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.full_name

    if action == "approve":
        update_user_status(target_id, "approved", 1, 1)
        add_audit_log(callback.from_user.id, admin_label, "approve", target_id)
        await apply_user_commands(target_id, is_admin=False, can_use_status=1, can_use_gemma=1)
        await callback.message.edit_text(f"✅ Пользователь <code>{target_id}</code> успешно одобрен!")
        try:
            await bot.send_message(target_id, "🎉 Поздравляем! Администратор одобрил ваш доступ к боту. Используйте /start.")
        except Exception: pass
    else:
        update_user_status(target_id, "rejected")
        add_audit_log(callback.from_user.id, admin_label, "reject", target_id)
        await callback.message.edit_text(f"❌ Заявка пользователя <code>{target_id}</code> отклонена.")
        try:
            await bot.send_message(target_id, "❌ Ваша заявка на доступ к боту была отклонена администратором.")
        except Exception: pass

    await callback.answer()

@dp.message(Command("status"))
async def command_status_handler(message: Message) -> None:
    user_data = await check_user_access(message)
    if not user_data: return
    
    if not user_data["can_use_status"]:
        return await message.reply("⚠️ Администратор ограничил вам доступ к функции просмотра системного отчета.")

    status_msg = await message.reply("⏳ <i>Fetching system metrics, please wait...</i>")
    try:
        cpu_usage, mem, disk_info = await fetch_system_metrics()
        cpu_bar = make_progress_bar(cpu_usage)
        ram_bar = make_progress_bar(mem.percent)
        disk_bar = make_progress_bar(disk_info["percent"])
        
        physical_cores = await asyncio.to_thread(psutil.cpu_count, logical=False) or "N/A"
        logical_cores = await asyncio.to_thread(psutil.cpu_count, logical=True) or "N/A"
        
        response = (
            f"🖥️ <b>SYSTEM STATUS REPORT</b>\n"
            f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
            f"⚙️ <b>CPU Utilization:</b>\n"
            f"{cpu_bar}\n"
            f"<i>Cores: {physical_cores} Physical | {logical_cores} Logical</i>\n\n"
            f"🧠 <b>Memory (RAM) Usage:</b>\n"
            f"{ram_bar}\n"
            f"<i>Used: {get_size_format(mem.used)} / {get_size_format(mem.total)}</i>\n\n"
            f"💾 <b>Disk Usage (<code>{DISK_PATH}</code>):</b>\n"
        )
        if disk_info["error"]:
            response += f"⚠️ <i>Error reading disk: {disk_info['error']}</i>\n"
        else:
            response += f"{disk_bar}\n<i>Used: {disk_info['used']} / {disk_info['total']} (Free: {disk_info['free']})</i>\n"
            
        response += f"\n🕒 <i>Last checked: {message.date.strftime('%Y-%m-%d %H:%M:%S')} UTC</i>"
        await status_msg.edit_text(response)
    except Exception as e:
        logger.exception("Failed to fetch system metrics")
        await status_msg.edit_text(f"❌ <b>Error:</b> Failed to retrieve system metrics.\n<code>{str(e)}</code>")

@dp.message(Command("admin"))
async def admin_panel_handler(message: Message):
    if not is_admin(message.from_user.id):
        return
        
    users = get_all_users()
    if not users:
        return await message.reply("👥 Список зарегистрированных пользователей пуст.")
        
    report = "👑 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ПОЛЬЗОВАТЕЛЯМИ</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    kb_list = []
    
    for u in users[:10]:
        status_icon = "⏳" if u['status'] == 'pending_approval' else "✅" if u['status'] == 'approved' else "❌"
        report += f"{status_icon} Логин: <code>{html.escape(u['login'])}</code> | ID: <code>{u['telegram_id']}</code>\n"
        report += f"└ Права: Сводка={u['can_use_status']} | ИИ={u['can_use_gemma']}\n\n"
        
        if u['status'] == 'approved':
            kb_list.append([InlineKeyboardButton(text=f"🚫 Бан {u['login']}", callback_data=f"ban_{u['telegram_id']}")])
        elif u['status'] == 'banned':
            kb_list.append([InlineKeyboardButton(text=f"🟢 Разбан {u['login']}", callback_data=f"unban_{u['telegram_id']}")])
            
    await message.reply(report, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))

@dp.callback_query(F.data.startswith("ban_") | F.data.startswith("unban_"))
async def process_user_ban_unban(callback: CallbackQuery):
    if not is_admin(callback.from_user.id): return
    action, target_id = callback.data.split("_")
    target_id = int(target_id)
    admin_label = f"@{callback.from_user.username}" if callback.from_user.username else callback.from_user.full_name

    if action == "ban":
        update_user_status(target_id, "banned")
        add_audit_log(callback.from_user.id, admin_label, "ban", target_id)
        await apply_user_commands(target_id, is_admin=False)
        await callback.answer("Пользователь забанен!")
    else:
        update_user_status(target_id, "approved", 1, 1)
        add_audit_log(callback.from_user.id, admin_label, "unban", target_id)
        await apply_user_commands(target_id, is_admin=False, can_use_status=1, can_use_gemma=1)
        await callback.answer("Пользователь разбанен!")
        
    users = get_all_users()
    report = "👑 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ПОЛЬЗОВАТЕЛЯМИ</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    kb_list = []
    for u in users[:10]:
        status_icon = "⏳" if u['status'] == 'pending_approval' else "✅" if u['status'] == 'approved' else "❌"
        report += f"{status_icon} Логин: <code>{html.escape(u['login'])}</code> | ID: <code>{u['telegram_id']}</code>\n"
        report += f"└ Права: Сводка={u['can_use_status']} | ИИ={u['can_use_gemma']}\n\n"
        if u['status'] == 'approved':
            kb_list.append([InlineKeyboardButton(text=f"🚫 Бан {u['login']}", callback_data=f"ban_{u['telegram_id']}")])
        elif u['status'] == 'banned':
            kb_list.append([InlineKeyboardButton(text=f"🟢 Разбан {u['login']}", callback_data=f"unban_{u['telegram_id']}")])
            
    await callback.message.edit_text(report, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))

# --- /model (выбор модели ИИ + переключатель памяти, на пользователя) ---

def _can_use_gemma(user_id: int) -> bool:
    if is_admin(user_id):
        return True
    user = get_user(user_id)
    return bool(user and user["status"] == "approved" and user["can_use_gemma"])


def model_keyboard(user_id: int) -> InlineKeyboardMarkup:
    current = get_model_pref(user_id)
    memory_on = get_memory_pref(user_id)

    rows = []
    for key in MODEL_TIERS:
        mark = "✅ " if key == current else "▫️ "
        rows.append([InlineKeyboardButton(
            text=f"{mark}{MODEL_LABELS[key]} · {MODEL_NAMES[key]}",
            callback_data=f"model_set_{key}",
        )])

    mem_label = "🧠 Память: 🟢 включена" if memory_on else "🧠 Память: 🔴 выключена"
    rows.append([InlineKeyboardButton(text=mem_label, callback_data="model_memory_toggle")])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_settings_text(user_id: int) -> str:
    pref = get_model_pref(user_id)
    memory_on = get_memory_pref(user_id)
    mem_line = (
        "🟢 <b>включена</b> — модель держится в памяти час после ответа."
        if memory_on else
        "🔴 <b>выключена</b> — модель выгружается через несколько минут, освобождая память."
    )
    return (
        "🤖 <b>Модель ИИ для чата</b>\n\n"
        f"Сейчас выбрано: <b>{html.escape(MODEL_LABELS[pref])}</b> "
        f"(<code>{html.escape(MODEL_NAMES[pref])}</code>)\n\n"
        "▫️ <b>Слабая</b> — быстрее, но проще.\n"
        "▫️ <b>Сильная</b> — умнее, медленнее.\n"
        "▫️ <b>Очень сильная</b> — самая умная, но самая медленная.\n"
        "Скорость не критична — выбирай ту, что даёт лучший ответ.\n\n"
        f"🧠 <b>Память:</b> {mem_line}"
    )


@dp.message(Command("model"))
async def command_model_handler(message: Message):
    user_data = await check_user_access(message)
    if not user_data:
        return
    if not user_data["can_use_gemma"]:
        return await message.reply("⚠️ Администратор отключил вам доступ к ИИ-модели.")
    await message.reply(
        model_settings_text(message.from_user.id),
        reply_markup=model_keyboard(message.from_user.id),
    )


@dp.callback_query(F.data.startswith("model_set_"))
async def handle_model_choice(callback: CallbackQuery):
    if not _can_use_gemma(callback.from_user.id):
        return await callback.answer("Нет доступа.", show_alert=True)

    pref = callback.data.removeprefix("model_set_")
    if pref not in MODEL_TIERS:
        return await callback.answer()
    set_model_pref(callback.from_user.id, pref)

    await callback.message.edit_text(
        model_settings_text(callback.from_user.id),
        reply_markup=model_keyboard(callback.from_user.id),
    )
    await callback.answer("Сохранено.")


@dp.callback_query(F.data == "model_memory_toggle")
async def handle_memory_toggle(callback: CallbackQuery):
    if not _can_use_gemma(callback.from_user.id):
        return await callback.answer("Нет доступа.", show_alert=True)

    new_state = not get_memory_pref(callback.from_user.id)
    set_memory_pref(callback.from_user.id, new_state)

    await callback.message.edit_text(
        model_settings_text(callback.from_user.id),
        reply_markup=model_keyboard(callback.from_user.id),
    )
    await callback.answer("Память включена." if new_state else "Память выключена.")


@dp.message(Command("gemma"))
async def gemma_chat_mode_on(message: Message, state: FSMContext):
    user_data = await check_user_access(message)
    if not user_data: return

    if not user_data["can_use_gemma"]:
        return await message.reply("⚠️ Администратор отключил вам доступ к ИИ-модели Gemma 2.")

    await state.set_state(GemmaStates.chatting)
    await message.reply("🧠 AI Chat Mode activated! Type your questions. To exit, type /bye")

@dp.message(GemmaStates.chatting, Command("bye"))
async def gemma_chat_mode_off(message: Message, state: FSMContext):
    await state.clear()
    await message.reply("👋 AI Chat Mode closed.")

async def _reply_with_gemma(message: Message, user_text: str):
    """Спільна логіка для текстового і голосового режиму /gemma."""
    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    # Історія (user + assistant) зберігається всередині ask_gemma — тут не дублюємо
    response = await ask_gemma(
        telegram_id=message.from_user.id,
        user_message=user_text,
        ollama_url=OLLAMA_URL,
        session=http_session,
        model=resolve_model(message.from_user.id),
        keep_alive=resolve_keep_alive(message.from_user.id),
    )

    # Відповідь моделі може містити символи < > &, які ламають HTML-розмітку Telegram.
    # Екрануємо й ріжемо на частини, бо ліміт повідомлення Telegram — 4096 символів.
    safe_response = html.escape(response)
    for chunk_start in range(0, len(safe_response), 4096):
        await message.reply(safe_response[chunk_start:chunk_start + 4096])

@dp.message(GemmaStates.chatting, F.text)
async def chat_with_gemma_handler(message: Message):
    user_data = await check_user_access(message)
    if not user_data: return

    await _reply_with_gemma(message, message.text)

@dp.message(GemmaStates.chatting, F.voice)
async def voice_with_gemma_handler(message: Message):
    user_data = await check_user_access(message)
    if not user_data: return

    if not user_data["can_use_gemma"]:
        return await message.reply("⚠️ Администратор отключил вам доступ к ИИ-модели Gemma 2.")

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")

    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
        tmp_path = tmp.name
    try:
        await bot.download(message.voice, destination=tmp_path)
        recognized_text = await asyncio.to_thread(transcribe_voice, tmp_path)
    except Exception:
        logger.exception("Failed to transcribe voice message")
        return await message.reply("❌ Не удалось распознать голосовое сообщение.")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    if not recognized_text:
        return await message.reply("🎤 Не удалось разобрать речь в сообщении. Попробуйте ещё раз.")

    await message.reply(f"🎤 <i>Распознано:</i> {html.escape(recognized_text)}")
    await _reply_with_gemma(message, recognized_text)

@dp.message(Command("history"))
async def command_history_handler(message: Message):
    user_data = await check_user_access(message)
    if not user_data: return

    if not user_data["can_use_gemma"]:
        return await message.reply("⚠️ Администратор отключил вам доступ к ИИ-модели Gemma 2.")

    history = get_chat_history(message.from_user.id, limit=20)
    if not history:
        return await message.reply("🗂️ История диалога с ИИ пуста.")

    lines = ["🗂️ <b>Последние сообщения диалога с ИИ</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"]
    for role, msg in history:
        speaker = "🧑 Вы" if role == "user" else "🧠 Gemma"
        text = msg if len(msg) <= 300 else msg[:300] + "…"
        lines.append(f"<b>{speaker}:</b> {html.escape(text)}")
    lines.append("\n<i>Чтобы очистить память — /forget</i>")

    full_text = "\n\n".join(lines)
    for chunk_start in range(0, len(full_text), 4096):
        await message.reply(full_text[chunk_start:chunk_start + 4096])

@dp.message(Command("forget"))
async def command_forget_handler(message: Message):
    user_data = await check_user_access(message)
    if not user_data: return

    if not user_data["can_use_gemma"]:
        return await message.reply("⚠️ Администратор отключил вам доступ к ИИ-модели Gemma 2.")

    clear_chat_history(message.from_user.id)
    await message.reply("🧹 Память диалога с ИИ очищена. Gemma больше не помнит предыдущий контекст.")

@dp.message(Command("tokens"))
async def command_tokens_handler(message: Message):
    if not is_admin(message.from_user.id):
        return

    per_user = get_token_usage_per_user()
    total = get_total_token_usage()

    if not per_user:
        return await message.reply("📈 Данных по расходу токенов пока нет.")

    report = "📈 <b>РАСХОД ТОКЕНОВ ПО ПОЛЬЗОВАТЕЛЯМ</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    for row in per_user:
        approx_mark = " <i>(есть оценочные значения)</i>" if row["has_estimated"] else ""
        report += (
            f"👤 <code>{html.escape(row['login'])}</code> | ID: <code>{row['telegram_id']}</code>\n"
            f"└ Запросов: {row['requests']} | Prompt: {row['prompt_tokens']} | "
            f"Completion: {row['completion_tokens']} | Всего: <b>{row['total_tokens']}</b>{approx_mark}\n\n"
        )

    report += (
        f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
        f"💯 <b>Всего по системе:</b> {total['requests']} запросов, "
        f"{total['total_tokens']} токенов (prompt: {total['prompt_tokens']}, completion: {total['completion_tokens']})"
    )
    await message.reply(report)

@dp.message(Command("auditlog"))
async def command_auditlog_handler(message: Message):
    if not is_admin(message.from_user.id):
        return

    entries = get_audit_log(limit=20)
    if not entries:
        return await message.reply("📜 Журнал действий админов пуст.")

    action_labels = {
        "approve": "✅ одобрил",
        "reject": "❌ отклонил",
        "ban": "🚫 забанил",
        "unban": "🟢 разбанил",
    }

    report = "📜 <b>ЖУРНАЛ ДЕЙСТВИЙ АДМИНОВ</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    for e in entries:
        target = html.escape(e["target_login"]) if e["target_login"] else str(e["target_telegram_id"])
        action_text = action_labels.get(e["action"], e["action"])
        report += (
            f"🕒 {e['created_at']}\n"
            f"<b>{html.escape(e['admin_label'] or 'unknown')}</b> {action_text} пользователя "
            f"<code>{target}</code>\n\n"
        )

    for chunk_start in range(0, len(report), 4096):
        await message.reply(report[chunk_start:chunk_start + 4096])

# --- ЗАПУСК ---
async def main() -> None:
    global http_session
    init_db()

    procfs_path = os.getenv("PROCFS_PATH")
    if procfs_path:
        # Застосовуємо тільки якщо шлях реально існує — інакше psutil впаде на
        # читанні /status з помилкою "No such file or directory: .../proc/stat".
        if os.path.isdir(procfs_path):
            logger.info(f"Using custom PROCFS_PATH: {procfs_path}")
            # psutil читає шлях до procfs з атрибута модуля, а не з env-змінної
            psutil.PROCFS_PATH = procfs_path
        else:
            logger.warning(
                f"PROCFS_PATH='{procfs_path}' does not exist — ignoring, "
                f"using the container's own /proc instead."
            )

    # Базовое "/"-меню для незарегистрированных/неизвестных чатов
    await bot.set_my_commands(
        build_command_list(is_admin=False),
        scope=BotCommandScopeDefault()
    )
    for admin_id in ADMIN_IDS:
        await apply_user_commands(admin_id, is_admin=True)

    http_session = aiohttp.ClientSession()
    logger.info("Starting secure bot polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await http_session.close()
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")