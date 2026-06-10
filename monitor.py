import os
import sys
import logging
import asyncio
from pathlib import Path
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
import psutil

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger("system_monitor")

# Load .env file manually if it exists to keep dependencies minimal and lightweight
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
if not TOKEN or TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
    logger.error("TELEGRAM_BOT_TOKEN is not set or is still default. Please update your .env file.")
    sys.exit("Error: TELEGRAM_BOT_TOKEN is not configured.")

DISK_PATH = os.getenv("MONITOR_DISK_PATH", "/")
logger.info(f"System Monitor Bot starting. Target disk path: '{DISK_PATH}'")

# Initialize Bot and Dispatcher according to aiogram v3 standards
bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

def make_progress_bar(percent: float, length: int = 10) -> str:
    """Generate a clean and professional ASCII progress bar"""
    filled_length = int(round(length * percent / 100))
    bar = '█' * filled_length + '░' * (length - filled_length)
    return f"<code>[{bar}]</code> <b>{percent:.1f}%</b>"

def get_size_format(b: float, factor: int = 1024) -> str:
    """Scale bytes to a human-readable format (e.g. GB)"""
    for unit in ["B", "KB", "MB", "GB", "TB", "PB"]:
        if b < factor:
            return f"{b:.2f} {unit}"
        b /= factor
    return f"{b:.2f} YB"

async def fetch_system_metrics():
    """Fetch system metrics asynchronously using asyncio.to_thread to prevent blocking the event loop"""
    # CPU usage over a 1.0s interval (measured in thread to prevent freezing the bot)
    cpu_usage = await asyncio.to_thread(psutil.cpu_percent, interval=1.0)
    
    # RAM utilization
    mem = await asyncio.to_thread(psutil.virtual_memory)
    
    # Disk space utilization
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
        disk_info = {
            "total": "N/A",
            "used": "N/A",
            "free": "N/A",
            "percent": 0.0,
            "error": str(e)
        }
        
    return cpu_usage, mem, disk_info

@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    """Handle the /start command"""
    welcome_text = (
        f"👋 <b>Welcome to the System Monitor Bot!</b>\n\n"
        f"I am a lightweight bot designed to monitor system resources in real-time.\n\n"
        f"<b>Available Commands:</b>\n"
        f"📊 /status - Get current CPU, RAM, and Disk utilization\n"
        f"ℹ️ /help - Display help menu"
    )
    await message.reply(welcome_text)

@dp.message(Command("help"))
async def command_help_handler(message: Message) -> None:
    """Handle the /help command"""
    help_text = (
        f"🛠️ <b>System Monitor Help Menu</b>\n\n"
        f"Use the following commands to interact with the bot:\n\n"
        f"📊 /status - Fetch current performance metrics of the host machine.\n"
        f"❓ /help - Show this help menu."
    )
    await message.reply(help_text)

@dp.message(Command("status"))
async def command_status_handler(message: Message) -> None:
    """Fetch and display host system metrics"""
    status_msg = await message.reply("⏳ <i>Fetching system metrics, please wait...</i>")
    
    try:
        cpu_usage, mem, disk_info = await fetch_system_metrics()
        
        cpu_bar = make_progress_bar(cpu_usage)
        ram_bar = make_progress_bar(mem.percent)
        disk_bar = make_progress_bar(disk_info["percent"])
        
        # Get physical and logical CPU core counts
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
            response += (
                f"{disk_bar}\n"
                f"<i>Used: {disk_info['used']} / {disk_info['total']} (Free: {disk_info['free']})</i>\n"
            )
            
        response += f"\n🕒 <i>Last checked: {message.date.strftime('%Y-%m-%d %H:%M:%S')} UTC</i>"
        
        await status_msg.edit_text(response)
        
    except Exception as e:
        logger.exception("Failed to fetch system metrics")
        await status_msg.edit_text(f"❌ <b>Error:</b> Failed to retrieve system metrics.\n<code>{str(e)}</code>")

async def main() -> None:
    # Set custom PROCFS_PATH env variable for psutil when running inside docker
    procfs_path = os.getenv("PROCFS_PATH")
    if procfs_path:
        logger.info(f"Using custom PROCFS_PATH: {procfs_path}")
        os.environ["PROCFS_PATH"] = procfs_path

    logger.info("Starting bot polling...")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
