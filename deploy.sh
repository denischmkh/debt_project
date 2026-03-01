#!/bin/bash
# Добавь эти строки в начало deploy.sh
exec > /tmp/deploy_debug.log 2>&1  # Записывать весь вывод в файл
set -x
# 1. Остановить выполнение при любой ошибке (set -e)
set -e

PROJECT_DIR="/root/debt_project"

echo "--- 🚀 Starting Deployment in $PROJECT_DIR ---"

# Переходим в папку проекта
cd "$PROJECT_DIR"

# 2. Подтягиваем изменения
echo "--- 📥 Pulling latest changes from Git ---"
git pull origin main

# 3. Пересобираем и запускаем контейнеры
# Используем 'up --detach' сразу (stop обычно не нужен, docker compose сам обновит измененные слои)
echo "--- 🛠 Rebuilding and starting Docker containers ---"
docker compose up --build --detach

# 4. Очистка старых образов (чтобы диск не забился)
echo "--- 🧹 Cleaning up unused Docker images ---"
docker image prune -f

echo "--- ✅ Deployment Finished Successfully! ---"