#!/usr/bin/env bash
# Prueba del despliegue con PostgreSQL (docker-compose.postgres.yml), junto con el escalado
# (dos workers escribiendo a la vez en la base de datos): la API y los workers usan
# PostgreSQL, la contraseña con caracteres especiales no aparece en los logs, la aplicación
# sobrevive a un reinicio de PostgreSQL y los datos persisten.
# Uso: scripts/postgres-smoke.sh   (desde la raíz del repositorio)
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT=chatbot-rag-postgres
PORT=${SMOKE_PORT:-18120}
BASE="http://127.0.0.1:${PORT}"
WORK=$(mktemp -d)
WORK_HOST=$(cd "$WORK" && (pwd -W 2>/dev/null || pwd))
COOKIES="$WORK/cookies.txt"
# Caracteres que habría que codificar si la contraseña fuera en la URL.
PASSWORD='Cl@ve:segura/%#?'
DATABASE_URL=postgresql+asyncpg://chatbot@postgres:5432/chatbot

cat > "$WORK/postgres.env" <<ENV
CHATBOT_LLM_PROVIDER=fake
CHATBOT_EMBEDDING_PROVIDER=fake
CHATBOT_ADMIN_PASSWORD=postgres-secreto
CHATBOT_SESSION_SECRET=postgres-session-secret
CHATBOT_RATE_LIMIT_LOGIN_PER_MINUTE=0
CHATBOT_DATABASE_URL=$DATABASE_URL
CHATBOT_DATABASE_PASSWORD=$PASSWORD
ENV

# CHATBOT_DATABASE_*: el compose las necesita también para sustituirlas (como con .env).
export CHATBOT_ENV_FILE="$WORK_HOST/postgres.env" CHATBOT_HTTP_PORT="$PORT" \
  CHATBOT_HTTP_BIND=127.0.0.1 CHATBOT_WORKERS=2 \
  CHATBOT_DATABASE_URL="$DATABASE_URL" CHATBOT_DATABASE_PASSWORD="$PASSWORD"
compose() {
  docker compose -p "$PROJECT" -f docker-compose.yml -f docker-compose.escalado.yml \
    -f docker-compose.postgres.yml "$@"
}

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- Logs ---"
    compose logs --no-color --tail 30 backend worker postgres || true
  fi
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "ERROR: $*" >&2; exit 1; }
step() { echo "==> $*"; }
admin() { curl -fsS -b "$COOKIES" "$@"; }
sql() { compose exec -T postgres psql -U chatbot -d chatbot -tAc "$1"; }
health() { curl -sS "$BASE/api/v1/health"; }
wait_health() {  # wait_health <ok|degraded>
  for _ in $(seq 1 60); do
    health 2>/dev/null | grep -q "\"status\":\"$1\"" && return 0
    sleep 1
  done
  fail "el estado no pasó a $1: $(health)"
}
# Mensajes sin acentos: en Windows, curl no envía los argumentos en UTF-8.
chat() {  # chat <mensaje>
  code=$(curl -sS -N -o "$WORK/chat.txt" -w '%{http_code}' -H 'Content-Type: application/json' \
    -d "{\"message\":\"$1\"}" "$BASE/api/v1/chat")
  [ "$code" = 200 ] && grep -q 'event: done' "$WORK/chat.txt" \
    || fail "el chat no terminó (HTTP $code): $(cat "$WORK/chat.txt")"
}

step "Arrancando API, nginx, PostgreSQL, Redis, Qdrant y 2 workers"
compose up -d --build --wait --wait-timeout 300
[ "$(compose ps --status running -q worker | wc -l)" -eq 2 ] || fail "no hay 2 workers en marcha"

step "La API ha migrado PostgreSQL y no usa SQLite"
revision=$(sql "SELECT version_num FROM alembic_version")
[ -n "$revision" ] || fail "PostgreSQL sin migrar"
echo "    revisión: $revision"
compose exec -T backend sh -c 'test ! -e /data/app.db' || fail "se ha creado un SQLite en /data"

step "El chat guarda las conversaciones en PostgreSQL"
chat "Hola desde PostgreSQL"
[ "$(sql "SELECT count(*) FROM conversations WHERE title = 'Hola desde PostgreSQL'")" = 1 ] \
  || fail "la conversación no está en PostgreSQL"
[ "$(sql "SELECT count(*) FROM question_log")" = 1 ] || fail "falta el registro de la pregunta"

step "Dos workers indexan a la vez en PostgreSQL"
curl -fsS -c "$COOKIES" -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"postgres-secreto"}' "$BASE/api/v1/admin/session" >/dev/null
files=()
for tema in vacaciones gastos teletrabajo formacion horarios permisos; do
  printf '# Política de %s\n\nNormas de %s para toda la plantilla.\n' "$tema" "$tema" > "$WORK/$tema.md"
  files+=(-F "files=@$WORK/$tema.md")
done
admin "${files[@]}" "$BASE/api/v1/admin/documents" >/dev/null
for _ in $(seq 1 60); do
  [ "$(sql "SELECT count(*) FROM documents WHERE status = 'ready'")" = 6 ] && break
  sleep 1
done
[ "$(sql "SELECT count(*) FROM documents WHERE status = 'ready'")" = 6 ] \
  || fail "no se indexaron los 6 documentos: $(sql "SELECT status, count(*) FROM documents GROUP BY status")"
workers=$(compose logs --no-color worker | grep -o 'worker-[0-9]' | sort -u | wc -l)
echo "    workers que han trabajado: $workers"

step "Con PostgreSQL parado, el estado es «degraded» y vuelve a «ok» al arrancarlo"
compose stop postgres >/dev/null
wait_health degraded
compose start postgres >/dev/null
wait_health ok

step "Tras reiniciar PostgreSQL, la API sigue respondiendo sin reiniciarse"
compose restart postgres >/dev/null
wait_health ok
chat "Tras el reinicio"
curl -fsS "$BASE/api/v1/conversations" | grep -q 'Tras el reinicio' \
  || fail "no se guardó la conversación tras el reinicio"

step "Los datos persisten al recrear los contenedores"
compose up -d --force-recreate --wait --wait-timeout 180 postgres backend >/dev/null
curl -fsS "$BASE/api/v1/conversations" | grep -q 'Hola desde PostgreSQL' || fail "se perdieron los datos"
admin "$BASE/api/v1/admin/documents/stats" | grep -q '"ready":6' || fail "se perdieron los documentos"

step "La contraseña no aparece en los logs"
compose logs --no-color backend worker > "$WORK/logs.txt"
grep -qF "$PASSWORD" "$WORK/logs.txt" && fail "la contraseña aparece en los logs"

echo "Prueba de PostgreSQL superada."
