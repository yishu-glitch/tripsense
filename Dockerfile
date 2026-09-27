# TripSense MVP — full API + static prototype in one process.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    PORT=8000

COPY pyproject.toml README.md ./
COPY src ./src
COPY web ./web
COPY data ./data

RUN pip install --no-cache-dir -e ".[api,llm]"

EXPOSE 8000

# Render/Railway/Fly set PORT; bind all interfaces for container networking.
CMD ["sh", "-c", "python -m tripsense.mvp --host 0.0.0.0 --port ${PORT:-8000}"]
