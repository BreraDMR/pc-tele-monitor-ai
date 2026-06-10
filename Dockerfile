FROM python:3.11-slim

# Set environment variables
# - PYTHONDONTWRITEBYTECODE: Prevents Python from writing .pyc files to disk
# - PYTHONUNBUFFERED: Ensures Python logs are flushed straight to the container log
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy dependency definition and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot script
COPY monitor.py .

# Run the system monitoring bot
CMD ["python", "monitor.py"]
