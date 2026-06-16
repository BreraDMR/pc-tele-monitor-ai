@echo off
chcp 65001 > nul
title Auto-Update ^& Launch ^| PC Tele Monitor AI

:: ===================== НАСТРОЙКИ =====================
set "PROJECT_DIR=C:\Users\user\pc-tele-monitor-ai"
set "REPO_URL=https://github.com/BreraDMR/pc-tele-monitor-ai.git"
set "BRANCH=main"
:: ====================================================

echo.
echo === Шаг 1: Ждём 10 секунд, пока Docker Desktop полностью загрузится...
timeout /t 10 /nobreak > nul

echo.
echo === Шаг 2: Переходим в папку проекта...
cd /d "%PROJECT_DIR%"

:: Если git-репозиторий ещё не подключён — инициализируем его БЕЗ удаления .env и data/
if not exist ".git" (
    echo .git не найден — подключаю репозиторий GitHub...
    git init
    git remote add origin "%REPO_URL%"
)

echo.
echo === Шаг 3: Полная синхронизация с GitHub ===
:: Забираем последнюю версию из репозитория
git fetch origin %BRANCH%
:: Затираем ВСЕ локальные изменения и приводим папку к точной версии с GitHub
git reset --hard origin/%BRANCH%
:: Удаляем лишние неотслеживаемые файлы (мусор).
:: ВАЖНО: .env и папка data/ (с базой) НЕ трогаются — они в .gitignore,
:: поэтому используем git clean без флага -x.
git clean -fd

echo.
echo === Шаг 4: Пересборка и запуск в Docker ===
:: compose сам остановит старый контейнер, пересоберёт образ и запустит заново
docker-compose down
docker-compose up --build -d

echo.
echo === Готово! Бот обновлён из GitHub и запущен. ===
echo Логи (Ctrl+C — выйти; контейнер продолжит работать в фоне):
docker-compose logs -f --tail=50
pause
