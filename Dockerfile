FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Сначала зависимости — лучше кэшируется при пересборке.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY workers ./workers

# Директория для данных SQLite (монтируется томом сервиса).
RUN mkdir -p /app/data \
    && addgroup --system app && adduser --system --ingroup app app \
    && chown -R app:app /app
USER app

EXPOSE 8000 50051

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
