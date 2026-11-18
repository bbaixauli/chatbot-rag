#!/usr/bin/env bash
# Prueba del despliegue escalado: Qdrant como índice, Redis como cola y dos workers de arq.
# Uso: scripts/escalado-smoke.sh   (desde la raíz del repositorio)
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT=chatbot-rag-escalado
PORT=${SMOKE_PORT:-18100}
BASE="http://127.0.0.1:${PORT}"
WORK=$(mktemp -d)
WORK_HOST=$(cd "$WORK" && (pwd -W 2>/dev/null || pwd))
COOKIES="$WORK/cookies.txt"

cat > "$WORK/escalado.env" <<ENV
CHATBOT_LLM_PROVIDER=fake
CHATBOT_EMBEDDING_PROVIDER=fake
CHATBOT_ADMIN_PASSWORD=escalado-secreto
CHATBOT_SESSION_SECRET=escalado-session-secret
CHATBOT_RATE_LIMIT_LOGIN_PER_MINUTE=0
ENV

export CHATBOT_ENV_FILE="$WORK_HOST/escalado.env" CHATBOT_HTTP_PORT="$PORT" \
  CHATBOT_HTTP_BIND=127.0.0.1 CHATBOT_WORKERS=2
compose() {
  docker compose -p "$PROJECT" -f docker-compose.yml -f docker-compose.escalado.yml "$@"
}

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- Logs ---"
    compose logs --no-color --tail 30 backend worker || true
  fi
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "ERROR: $*" >&2; exit 1; }
step() { echo "==> $*"; }
admin() { curl -fsS -b "$COOKIES" "$@"; }
stats() { admin "$BASE/api/v1/admin/documents/stats"; }
wait_ready() {  # wait_ready <documentos>
  for _ in $(seq 1 60); do
    stats | grep -q "\"ready\":$1" && return 0
    sleep 1
  done
  fail "los documentos no se indexaron: $(stats)"
}

step "Arrancando API, nginx, Redis, Qdrant y 2 workers"
compose up -d --build --wait --wait-timeout 300
[ "$(compose ps --status running -q worker | wc -l)" -eq 2 ] || fail "no hay 2 workers en marcha"

step "Los documentos subidos los indexan los workers"
curl -fsS -c "$COOKIES" -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"escalado-secreto"}' "$BASE/api/v1/admin/session" >/dev/null
for tema in vacaciones gastos teletrabajo; do
  printf '# Política de %s\n\nNormas de %s para toda la plantilla.\n' "$tema" "$tema" > "$WORK/$tema.md"
done
admin -F "files=@$WORK/vacaciones.md" -F "files=@$WORK/gastos.md" -F "files=@$WORK/teletrabajo.md" \
  "$BASE/api/v1/admin/documents" >/dev/null
wait_ready 3
compose logs --no-color worker | grep -q "index_document" || fail "los workers no procesaron trabajos"
stats | grep -q '"chunks":[1-9]' || fail "Qdrant no tiene fragmentos: $(stats)"

step "El chat recupera fragmentos desde Qdrant"
curl -fsS -N -H 'Content-Type: application/json' \
  -d '{"message":"normas de teletrabajo para la plantilla"}' "$BASE/api/v1/chat" > "$WORK/chat.txt"
grep -q 'event: sources' "$WORK/chat.txt" || fail "sin fuentes: $(cat "$WORK/chat.txt")"
grep -q 'teletrabajo.md' "$WORK/chat.txt" || fail "fuente inesperada: $(cat "$WORK/chat.txt")"

step "Eliminar un documento lo quita del índice"
teletrabajo=$(admin "$BASE/api/v1/admin/documents" | tr '{' '\n' | grep '"filename":"teletrabajo.md"' \
  | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
[ -n "$teletrabajo" ] || fail "no se encontró el documento"
admin -X DELETE "$BASE/api/v1/admin/documents/$teletrabajo" >/dev/null
curl -fsS -N -H 'Content-Type: application/json' \
  -d '{"message":"normas de teletrabajo para la plantilla"}' "$BASE/api/v1/chat" > "$WORK/chat2.txt"
grep -q 'teletrabajo.md' "$WORK/chat2.txt" && fail "el documento eliminado sigue en el índice"

step "Copia de seguridad y restauración (vacía Qdrant y reindexan los workers)"
created=$(compose exec -T backend python -m app.backup create)
echo "$created" | grep -q 'Copia creada' || fail "no se creó la copia: $created"
mkdir -p "$WORK/backups"
compose cp backend:/data/backups/. "$WORK/backups" >/dev/null
archive=$(cd "$WORK/backups" && ls chatbot-backup-*.tar.gz | head -1)
compose stop backend worker >/dev/null 2>&1
restored=$(MSYS_NO_PATHCONV=1 compose run --rm -T --no-deps -v "$WORK_HOST/backups:/restaurar:ro" \
  --entrypoint python backend -m app.backup restore "/restaurar/$archive" --force)
echo "$restored" | grep -q '2 documentos se reindexarán' || fail "restauración inesperada: $restored"
compose up -d --wait --wait-timeout 180 backend worker
curl -fsS -c "$COOKIES" -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"escalado-secreto"}' "$BASE/api/v1/admin/session" >/dev/null
wait_ready 2
stats | grep -q '"documents":2' || fail "número de documentos inesperado: $(stats)"

echo "Prueba de escalado superada."
