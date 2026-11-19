# Imagen del backend (API FastAPI). Se construye desde la carpeta backend/.
FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:0.13.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Extras opcionales, p. ej. --build-arg UV_EXTRAS=local-embeddings
ARG UV_EXTRAS=""

# Primero solo las dependencias, para aprovechar la caché de capas.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project ${UV_EXTRAS:+--extra $UV_EXTRAS}

COPY alembic.ini ./
COPY alembic ./alembic
COPY app ./app
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin chatbot \
    && mkdir -p /data \
    && chown chatbot:chatbot /data \
    && chmod +x /usr/local/bin/docker-entrypoint.sh

ENV PATH=/app/.venv/bin:$PATH \
    CHATBOT_DATA_DIR=/data \
    CHATBOT_ENVIRONMENT=production \
    CHATBOT_LOG_FORMAT=json \
    HF_HOME=/data/modelos

USER chatbot
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]

ENTRYPOINT ["docker-entrypoint.sh"]
