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

# Импортируем созданные модули локальной БД и ИИ напрямую
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
ADMIN_ID = os.getenv("ADMIN_TELEGRAM_ID") # Твой Telegram ID числом
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

# Инициализируем состояния FSM для регистрации
class RegisterStates(StatesGroup):
    waiting_for_login = State()
    waiting_for_password = State()

# Инициализируем состояния FSM для AI чата
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
async def check_user_access(message: Message) -> dict | None:
    """Проверяет права пользователя. Возвращает запись из БД, если доступ есть."""
    if ADMIN_ID and message.from_user.id == ADMIN_ID:
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
    is_admin = ADMIN_ID and message.from_user.id == ADMIN_ID
    
    welcome_text = f"👋 <b>Welcome to the Secure System Monitor Bot!</b>\n\n"
    
    if is_admin:
        welcome_text += "👑 Вы являетесь администратором системы.\nДоступные команды:\n📊 /status - Просмотр метрик\n🛠️ /admin - Панель управления\n🧠 /gemma - Чат с AI"
    elif user and user["status"] == "approved":
        welcome_text += "✅ Вы успешно авторизованы.\n\n<b>Доступные функции:</b>\n"
        if user["can_use_status"]: welcome_text += "📊 /status - Получить системный отчет\n"
        if user["can_use_gemma"]: welcome_text += "🧠 /gemma - Чат с AI"
    else:
        welcome_text += "🔒 Доступ закрыт. Чтобы начать использование, пройдите регистрацию:\n📝 /register"
        
    await message.reply(welcome_text)

# Процесс регистрации (FSM)
@dp.message(Command("register"))
async def start_registration(message: Message, state: FSMContext):
    if ADMIN_ID and message.from_user.id == ADMIN_ID:
        return await message.reply("👑 Админу не нужно регистрироваться!")
        
    user = get_user(message.from_user.id)
    if user:
        return await message.reply(f"Вы уже зарегистрированы. Статус: <b>{user['status']}</b>")
        
    await state.set_state(RegisterStates.waiting_for_login)
    await message.reply("📝 Начнем регистрацию.\nПридумайте и отправьте желаемый <b>Логин</b>:")

@dp.message(RegisterStates.waiting_for_login)
async def process_login(message: Message, state: FSMContext):
    login = message.text.strip()
    if len(login) < 3:
        return await message.reply("❌ Логин должен содержать не менее 3 символов. Попробуйте еще раз:")
        
    if is_login_taken(login):
        return await message.reply("❌ Этот логин уже занят. Придумайте другой:")
        
    await state.update_data(chosen_login=login)
    await state.set_state(RegisterStates.waiting_for_password)
    await message.reply("🔑 Теперь придумайте и отправьте надежный <b>Пароль</b>:")

@dp.message(RegisterStates.waiting_for_password)
async def process_password(message: Message, state: FSMContext):
    password = message.text.strip()
    if len(password) < 4:
        return await message.reply("❌ Пароль слишком короткий. Придумайте более надежный:")
        
    data = await state.get_data()
    login = data['chosen_login']
    
    # Сохраняем в бд
    success = register_user(
        telegram_id=message.from_user.id,
        username=message.from_user.username or message.from_user.full_name,
        login=login,
        password_plain=password
    )
    
    await state.clear()
    
    if success:
        await message.reply("🎉 Регистрация завершена! Ваша учетная запись отправлена Администратору на одобрение. Ожидайте.")
        
        # Уведомляем админа
        if ADMIN_ID:
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_{message.from_user.id}"),
                    InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_{message.from_user.id}")
                ]
            ])
            await bot.send_message(
                ADMIN_ID,
                f"🔔 <b>Новая заявка на регистрацию!</b>\n\n"
                f"👤 Пользователь: {message.from_user.full_name}\n"
                f"🏷️ Username: @{message.from_user.username or 'none'}\n"
                f"🆔 ID: <code>{message.from_user.id}</code>\n"
                f"📝 Выбранный логин: <code>{login}</code>",
                reply_markup=kb
            )
    else:
        await message.reply("❌ Произошла ошибка при сохранении данных. Попробуйте снова через /register")

# --- ОБРАБОТКА РЕШЕНИЙ АДМИНА С КНОПОК ---
@dp.callback_query(F.data.startswith("approve_") | F.data.startswith("reject_"))
async def handle_approval_buttons(callback: CallbackQuery):
    if ADMIN_ID and callback.from_user.id != ADMIN_ID:
        return await callback.answer("У вас нет прав на это действие.", show_alert=True)
        
    action, target_id = callback.data.split("_")
    target_id = int(target_id)
    
    if action == "approve":
        update_user_status(target_id, "approved", 1, 1)
        await callback.message.edit_text(f"✅ Пользователь <code>{target_id}</code> успешно одобрен!")
        try:
            await bot.send_message(target_id, "🎉 Поздравляем! Администратор одобрил ваш доступ к боту. Используйте /start.")
        except Exception: pass
    else:
        update_user_status(target_id, "rejected")
        await callback.message.edit_text(f"❌ Заявка пользователя <code>{target_id}</code> отклонена.")
        try:
            await bot.send_message(target_id, "❌ Ваша заявка на доступ к боту была отклонена администратором.")
        except Exception: pass
        
    await callback.answer()

# --- КОМАНДА STATUS (С ПРОВЕРКОЙ ДОСТУПА) ---
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

# --- КОМАНДНАЯ ПАНЕЛЬ АДМИНИСТРАТОРА ---
@dp.message(Command("admin"))
async def admin_panel_handler(message: Message):
    if not ADMIN_ID or message.from_user.id != ADMIN_ID:
        return # Игнорируем обычных пользователей
        
    users = get_all_users()
    if not users:
        return await message.reply("👥 Список зарегистрированных пользователей пуст.")
        
    report = "👑 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ПОЛЬЗОВАТЕЛЯМИ</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    kb_list = []
    
    for u in users[:10]: # Показываем топ-10 для компактности
        status_icon = "⏳" if u['status'] == 'pending_approval' else "✅" if u['status'] == 'approved' else "❌"
        report += f"{status_icon} Логин: <code>{u['login']}</code> | ID: <code>{u['telegram_id']}</code>\n"
        report += f"└ Права: Сводка={u['can_use_status']} | ИИ={u['can_use_gemma']}\n\n"
        
        if u['status'] == 'approved':
            kb_list.append([InlineKeyboardButton(text=f"🚫 Бан {u['login']}", callback_data=f"ban_{u['telegram_id']}")])
        elif u['status'] == 'banned':
            kb_list.append([InlineKeyboardButton(text=f"🟢 Разбан {u['login']}", callback_data=f"unban_{u['telegram_id']}")])
            
    await message.reply(report, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))

@dp.callback_query(F.data.startswith("ban_") | F.data.startswith("unban_"))
async def process_user_ban_unban(callback: CallbackQuery):
    if callback.from_user.id != ADMIN_ID: return
    action, target_id = callback.data.split("_")
    target_id = int(target_id)
    
    if action == "ban":
        update_user_status(target_id, "banned")
        await callback.answer("Пользователь забанен!")
    else:
        update_user_status(target_id, "approved", 1, 1)
        await callback.answer("Пользователь разбанен!")
        
    # Обновляем панель управления
    users = get_all_users()
    report = "👑 <b>ПАНЕЛЬ УПРАВЛЕНИЯ ПОЛЬЗОВАТЕЛЯМИ</b>\n⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n\n"
    kb_list = []
    for u in users[:10]:
        status_icon = "⏳" if u['status'] == 'pending_approval' else "✅" if u['status'] == 'approved' else "❌"
        report += f"{status_icon} Логин: <code>{u['login']}</code> | ID: <code>{u['telegram_id']}</code>\n"
        report += f"└ Права: Сводка={u['can_use_status']} | ИИ={u['can_use_gemma']}\n\n"
        if u['status'] == 'approved':
            kb_list.append([InlineKeyboardButton(text=f"🚫 Бан {u['login']}", callback_data=f"ban_{u['telegram_id']}")])
        elif u['status'] == 'banned':
            kb_list.append([InlineKeyboardButton(text=f"🟢 Разбан {u['login']}", callback_data=f"unban_{u['telegram_id']}")])
            
    await callback.message.edit_text(report, reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_list))

# --- ИНТЕГРАЦИЯ С GEMMA 2 9B ---
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

@dp.message(GemmaStates.chatting, F.text)
async def chat_with_gemma_handler(message: Message):
    user_data = await check_user_access(message)
    if not user_data: return # Should not happen if FSM is correctly managed, but for safety

    await bot.send_chat_action(chat_id=message.chat.id, action="typing")
    
    response = await ask_gemma(
        telegram_id=message.from_user.id,
        user_message=message.text,
        ollama_url=OLLAMA_URL
    )
    
    add_chat_message(message.from_user.id, "user", message.text)
    add_chat_message(message.from_user.id, "assistant", response)
    
    await message.reply(response)

# --- ЗАПУСК ---
async def main() -> None:
    # Инициализируем БД при старте
    init_db()
    
    procfs_path = os.getenv("PROCFS_PATH")
    if procfs_path:
        logger.info(f"Using custom PROCFS_PATH: {procfs_path}")
        os.environ["PROCFS_PATH"] = procfs_path

    logger.info("Starting secure bot polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")