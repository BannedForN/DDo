FROM python:3.11-slim

WORKDIR /app

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем весь остальной код
COPY . .

# Запускаем FastAPI на порту 8000 внутри контейнера
CMD["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]