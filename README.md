<div align="center">

# 🖥️ PC Tele Monitor AI

**A Telegram bot for keeping an eye on a headless PC — live CPU / RAM / disk metrics, approval-gated access, and an optional local-AI chat, all in one container.**

[![Python](https://img.shields.io/badge/Python-3776AB?logo=python&logoColor=white&style=for-the-badge)](requirements.txt)
[![aiogram](https://img.shields.io/badge/aiogram-Telegram-2CA5E0?logo=telegram&logoColor=white&style=for-the-badge)](requirements.txt)
[![psutil](https://img.shields.io/badge/psutil-system%20metrics-4B8BBE?style=for-the-badge)](monitor.py)
[![Ollama](https://img.shields.io/badge/Ollama-Gemma%202-000000?logo=ollama&logoColor=white&style=for-the-badge)](gemma.py)
[![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white&style=for-the-badge)](docker-compose.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green?style=for-the-badge)](LICENSE)

</div>

> My first home-lab project — the starting point for [homelab-wsl](https://github.com/BreraDMR/homelab-wsl).

## Why this exists

A headless under-desk PC has no screen to glance at, and SSH-ing in just to check
if it's overloaded is friction. The problems this bot solves:

- **No quick way to check a headless box's load.** A single `/status` message
  returns live CPU / RAM / disk usage from anywhere, over a chat app you already
  have open — no SSH, no monitoring stack to host.
- **A status bot shouldn't be open to the world.** Access is **approval-gated**:
  strangers can `/register`, but only the admin's one-tap approval lets them in,
  and the admin can ban/unban — so the bot stays private without sharing a secret.
- **Cloud LLM chat means a bill and data leaving the box.** The optional AI chat
  runs a **local** model (Gemma 2 via Ollama) on the same machine — free, private,
  and offline-capable — instead of calling a paid API.

## Overview

PC Tele Monitor AI is a Telegram bot designed to monitor system metrics (CPU, RAM, Disk usage) and provide an interactive chat interface powered by the Gemma 2 9B AI model. The bot allows authorized users to query system status and engage in AI-driven conversations, with an administrative panel for user management and access control. It is built using Python, `aiogram` for the Telegram bot, `psutil` for system monitoring, `SQLite` for user and chat history management, and integrates with the `Ollama` platform for local AI model inference.

## Features

- **System Metrics Monitoring:** Get real-time reports on CPU, RAM, and disk usage.
- **User Registration and Approval:** Secure user access with a registration process requiring administrator approval.
- **Admin Panel:** Administrators can manage registered users, approve/reject access, and ban/unban users.
- **AI Chat Integration:** Interact with the Gemma 2 9B AI model (via Ollama) for general queries.
- **Dockerized Deployment:** Easy setup and deployment using Docker and Docker Compose.
- **Persistent Chat History:** Stores AI chat conversations in an SQLite database.

## Installation

To set up and run the PC Tele Monitor AI bot, follow these steps:

### Prerequisites

- Docker and Docker Compose installed on your system.
- A Telegram bot token from BotFather.
- (Optional) Ollama installed and running with the `gemma2:9b` or `gemma2` model pulled, if you wish to use the AI chat feature.

### 1. Clone the repository

```bash
git clone https://github.com/BreraDMR/pc-tele-monitor-ai.git
cd pc-tele-monitor-ai
```

### 2. Configure Environment Variables

Create a `.env` file in the `pc-tele-monitor-ai/` directory based on `.env.example`:

```ini
TELEGRAM_BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
ADMIN_TELEGRAM_ID=YOUR_TELEGRAM_ID_NUMBER
OLLAMA_URL=http://host.docker.internal:11434  # Or your Ollama instance URL
MONITOR_DISK_PATH=/                             # Disk path to monitor (e.g., / for Linux, C:\ for Windows)
PROCFS_PATH=/proc                               # Optional: for custom /proc path in some Docker setups
```

- Replace `YOUR_TELEGRAM_BOT_TOKEN` with your actual Telegram bot token.
- Replace `YOUR_TELEGRAM_ID_NUMBER` with your Telegram user ID (as a number), which will be granted admin privileges.
- Adjust `OLLAMA_URL` if your Ollama instance is not accessible at `http://host.docker.internal:11434` (e.g., if Ollama is running on a different machine or directly on the host and not exposed via `host.docker.internal`).
- Set `MONITOR_DISK_PATH` to the root of the disk you want to monitor (e.g., `/` for Linux/macOS, `C:\` for Windows).
- `PROCFS_PATH` is usually not needed unless you are running in a specialized Docker environment where `/proc` is mounted differently.

### 3. Build and Run with Docker Compose

```bash
docker-compose up --build -d
```

This command will:
- Build the Docker image for the bot.
- Start the `system_monitor_bot` container.
- Initialize the SQLite database (`system_monitor_bot.db`) for user data and chat history.

### 4. Verify Ollama (if using AI chat)

If you intend to use the AI chat feature, ensure Ollama is running and has the `gemma2:9b` or `gemma2` model available. You can pull the model using:

```bash
ollama pull gemma2:9b
# or
ollama pull gemma2
```

## Usage

Once the bot is running and you have been approved by the administrator (if you are not the admin):

- Send `/start` to the bot to see the welcome message and available commands.
- **`/register`**: (For non-admin users) Initiate the registration process by providing a desired login and password. Your request will be sent to the administrator for approval.
- **`/status`**: Get a report on the system's CPU, RAM, and disk usage.
- **`/gemma`**: Enter AI chat mode to converse with the Gemma 2 9B model. Type `/bye` to exit chat mode.
- **`/admin`**: (Admin only) Access the administration panel to manage user approvals, rejections, bans, and unbans.

## Technologies Used

- **Python**: Core programming language.
- **aiogram**: Asynchronous Telegram Bot API framework.
- **psutil**: Cross-platform library for retrieving process and system utilization (CPU, memory, disks, network, sensors) in Python.
- **SQLite**: Lightweight, file-based database for storing user information and chat history.
- **Ollama**: Platform for running large language models locally.
- **Gemma 2 9B**: Large Language Model by Google, used for AI chat capabilities.
- **Docker**: Containerization platform for easy deployment.
- **Docker Compose**: Tool for defining and running multi-container Docker applications.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
