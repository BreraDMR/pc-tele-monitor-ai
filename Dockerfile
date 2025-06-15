FROM python:3.11-slim

# Отключаем создание .pyc файлов и включаем вывод логов
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Копируем зависимости
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем ВСЕ файлы проекта в текущую директорию контейнера (/app)
COPY . .

# Запускаем бота
CMD ["python", "monitor.py"]