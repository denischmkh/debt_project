FROM python:3.14-slim

# Устанавливаем рабочую директорию в корне контейнера
WORKDIR /project

# Копируем requirements из папки app в корень сборки
COPY app/requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Копируем всё содержимое проекта (включая папку app) в контейнер
COPY . .

# Запускаем uvicorn, указывая путь к объекту app внутри модуля app.main
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]