@echo off
chcp 65001 > nul

echo 📥 Шаг 1: Переходим в папку с ботом...
cd /d C:\Users\user\pc-tele-monitor-ai

:: Ждем, пока Docker Desktop полностью раздуплится в фоне
echo Ожидание запуска Docker...
timeout /t 15 /nobreak

echo.
echo 🔄 Шаг 2: Очистка папки (кроме важных файлов)...
for %%i in (*) do (
    if not "%%~nxi"==".env" if not "%%~nxi"=="bot_update.bat" del /f /q "%%i"
)
for /d %%i in (*) do (
    if not "%%~nxi"==".git" rd /s /q "%%i"
)

echo 🔄 Шаг 2.1: Сброс Git и полное обновление из репозитория...
git reset --hard
git pull origin main

echo.
echo 🏗️ Шаг 3: Пересборка и запуск через Docker Compose...
:: compose сам остановит старый контейнер, пересоберет образ и запустит его
docker-compose down
docker-compose up --build -d

echo.
echo 🎉 Готово! Бот успешно обновлён и запущен.
echo 📺 Логи запуска (нажми Ctrl+C для выхода):
:: Меняем имя контейнера на то, которое генерирует docker-compose (обычно папка_сервис_1)
docker-compose logs -f --tail=50
pause