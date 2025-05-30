# syntax=docker/dockerfile:1
FROM python:3.12-slim

# 1️⃣  Переменные окружения
ENV PYTHONDONTWRITEBYTECODE=1 \   # не создавать .pyc → меньше слой
    PYTHONUNBUFFERED=1            # сразу писать логи в stdout

# 2️⃣  Рабочая директория внутри контейнера
WORKDIR /app

# 3️⃣  Слой «только зависимости»  ─ кешируется
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 4️⃣  Код бота
COPY bot/ bot/

# 5️⃣  (Опционально) Локальные картинки
#    Нужен ТОЛЬКО если в викторине используются "image_file": "xxx.png"
COPY bot/images/ bot/images/
COPY bot/fonts/ bot/fonts/

# 6️⃣  Команда по умолчанию
CMD ["python", "-m", "bot"]