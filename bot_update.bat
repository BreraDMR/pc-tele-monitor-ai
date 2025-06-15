@echo off
chcp 65001 > nul

echo 📥 Шаг 1: Переходим в папку с ботом...
cd /d C:\Users\user\pc-tele-monitor-ai

echo.
echo 🔄 Шаг 2: Очистка папки (кроме важных файлов)...
:: Удаляем все файлы и папки, КРОМЕ .env, .git и самого скрипта обновления
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
echo 🗑️ Шаг 3: Удаляем старый контейнер...
docker rm -f tele-bot-container

echo.
echo 🏗️ Шаг 4: Собираем новый образ...
docker build -t system-monitor-bot .

echo.
echo 🚀 Шаг 5: Запускаем обновлённого бота...
docker run -d --restart unless-stopped --name tele-bot-container --add-host=host.docker.internal:host-gateway -v C:\:/host_c --env-file .env system-monitor-bot

echo.
echo 🎉 Готово! Бот успешно обновлён и запущен.
echo 📺 Логи запуска (нажми Ctrl+C для выхода):
docker logs -f tele-bot-container
pause