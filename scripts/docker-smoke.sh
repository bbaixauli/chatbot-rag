#!/usr/bin/env bash
# Prueba de humo del despliegue con Docker: construye las imágenes, arranca el compose con
# modelos simulados y comprueba el recorrido principal a través de nginx.
# Uso: scripts/docker-smoke.sh   (desde la raíz del repositorio)
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT=chatbot-rag-smoke
PORT=${SMOKE_PORT:-18080}
BASE="http://127.0.0.1:${PORT}"
WORK=$(mktemp -d)
ENV_FILE="$WORK/smoke.env"
COOKIES="$WORK/cookies.txt"

cat > "$ENV_FILE" <<ENV
CHATBOT_LLM_PROVIDER=fake
CHATBOT_EMBEDDING_PROVIDER=fake
CHATBOT_ADMIN_PASSWORD=smoke-secreto
CHATBOT_SESSION_SECRET=smoke-session-secret
CHATBOT_RATE_LIMIT_LOGIN_PER_MINUTE=3
ENV

export CHATBOT_ENV_FILE="$ENV_FILE" CHATBOT_HTTP_PORT="$PORT" CHATBOT_HTTP_BIND=127.0.0.1
compose() { docker compose -p "$PROJECT" "$@"; }

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- Logs de los contenedores ---"
    compose logs --no-color --tail 80 || true
  fi
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "ERROR: $*" >&2; exit 1; }
step() { echo "==> $*"; }

step "Construyendo y arrancando"
compose up -d --build --wait --wait-timeout 180

step "La SPA se sirve y las rutas del cliente devuelven index.html"
curl -fsS "$BASE/" | grep -q '<div id="root">' || fail "index.html no servido"
curl -fsS "$BASE/admin/documents" | grep -q '<div id="root">' || fail "sin fallback de la SPA"

step "La API responde a través de nginx con id de petición"
headers=$(curl -fsS -D - -o "$WORK/health.json" "$BASE/api/v1/health")
grep -q '"status":"ok"' "$WORK/health.json" || fail "health no ok: $(cat "$WORK/health.json")"
echo "$headers" | grep -qi '^x-request-id:' || fail "falta X-Request-ID"

step "El chat responde en streaming"
curl -fsS -N -H 'Content-Type: application/json' -d '{"message":"Hola desde Docker"}' \
  "$BASE/api/v1/chat" > "$WORK/chat.txt"
grep -q 'event: done' "$WORK/chat.txt" || fail "el chat no terminó: $(cat "$WORK/chat.txt")"
grep -q 'Hola desde Docker' "$WORK/chat.txt" || fail "respuesta inesperada"

step "Administración: inicio de sesión y subida de un documento"
curl -fsS -c "$COOKIES" -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"smoke-secreto"}' "$BASE/api/v1/admin/session" >/dev/null
printf '# Vacaciones\n\nCada persona dispone de 23 días laborables de vacaciones al año.\n' \
  > "$WORK/vacaciones.md"
curl -fsS -b "$COOKIES" -F "files=@$WORK/vacaciones.md" "$BASE/api/v1/admin/documents" >/dev/null
for _ in $(seq 1 30); do
  curl -fsS -b "$COOKIES" "$BASE/api/v1/admin/documents" | grep -q '"status":"ready"' && break
  sleep 1
done
curl -fsS -b "$COOKIES" "$BASE/api/v1/admin/documents" | grep -q '"status":"ready"' \
  || fail "el documento no se indexó"

step "Límite de intentos de inicio de sesión"
codes=""
for _ in 1 2 3 4; do
  codes+=$(curl -s -o /dev/null -w '%{http_code} ' -H 'Content-Type: application/json' \
    -d '{"username":"admin","password":"mala"}' "$BASE/api/v1/admin/session")
done
echo "    códigos: $codes"
[[ "$codes" == *429* ]] || fail "no se aplicó el límite de peticiones"

step "Copia de seguridad"
compose exec -T backend python -m app.backup create --keep 3 | grep -q 'Copia creada' \
  || fail "no se creó la copia"

step "Los datos persisten tras recrear el backend"
compose up -d --force-recreate --wait --wait-timeout 120 backend
curl -fsS "$BASE/api/v1/conversations" | grep -q 'Hola desde Docker' || fail "se perdieron los datos"
compose exec -T backend sh -c 'ls /data/backups/*.tar.gz' >/dev/null || fail "se perdió la copia"

step "Logs en JSON"
compose logs --no-color backend | grep -q '"logger": "app.access"' || fail "logs sin formato JSON"

echo "Prueba de humo superada."
