FROM python:3.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN groupadd --gid 10001 gateway \
    && useradd --uid 10001 --gid gateway --no-create-home --shell /usr/sbin/nologin gateway

COPY app ./app
COPY alembic.ini ./
COPY alembic ./alembic

USER gateway:gateway

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--lifespan", "on"]
