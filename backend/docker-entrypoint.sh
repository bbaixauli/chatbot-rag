#!/bin/sh
# Aplica las migraciones pendientes y arranca la API.
set -eu

alembic upgrade head

# Un único proceso: la indexación en segundo plano y el límite de peticiones viven en memoria.
# Solo nginx (red interna de Docker) llega al backend, así que se confía en sus cabeceras
# X-Forwarded-* para conocer la IP real del cliente.
exec uvicorn app.main:app \
    --host 0.0.0.0 \
    --port 8000 \
    --proxy-headers \
    --forwarded-allow-ips "${CHATBOT_FORWARDED_ALLOW_IPS:-*}" \
    --no-access-log \
    "$@"
