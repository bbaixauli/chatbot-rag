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
# Sin `grep -q` tras `compose logs`: con pipefail, si grep sale al encontrarlo, compose
# recibe SIGPIPE y la tubería falla aunque el texto esté (fallo intermitente con logs largos).
compose logs --no-color worker | grep "index_document" >/dev/null || fail "los workers no procesaron trabajos"
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

step "«Reindexar todo» con los workers en marcha (la API vacía Qdrant y ellos lo rellenan)"
admin -X POST "$BASE/api/v1/admin/documents/reindex" | grep -q '"scheduled":2' \
  || fail "no se programó la reindexación"
wait_ready 2
stats | grep -q '"error"' && fail "documentos con error tras reindexar: $(stats)"
stats | grep -q '"chunks":[1-9]' || fail "Qdrant quedó vacío tras reindexar: $(stats)"
curl -fsS -N -H 'Content-Type: application/json' \
  -d '{"message":"normas de vacaciones para la plantilla"}' "$BASE/api/v1/chat" > "$WORK/chat3.txt"
grep -q 'vacaciones.md' "$WORK/chat3.txt" || fail "sin fuentes tras reindexar: $(cat "$WORK/chat3.txt")"

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

step "Documento web: alta por la API, actualización por los workers"
# Página servida desde el contenedor de la API (los workers la alcanzan por su nombre).
web_page() {  # web_page <texto>
  printf '<html><head><title>Politica de viajes</title></head><body><main>'
  printf '<h1>Politica de viajes</h1><p>%s</p></main></body></html>' "$1"
}
web_page "La dieta nacional es de 53 euros para toda la plantilla." \
  | MSYS_NO_PATHCONV=1 compose exec -T backend sh -c 'mkdir -p /tmp/web && cat > /tmp/web/viajes.html'
MSYS_NO_PATHCONV=1 compose exec -d backend python -m http.server 8099 --directory /tmp/web
sleep 2
added=$(admin -H 'Content-Type: application/json' \
  -d '{"urls":["http://backend:8099/viajes.html"]}' "$BASE/api/v1/admin/documents/urls")
echo "$added" | grep -q '"error":null' || fail "no se añadió la página: $added"
web=$(echo "$added" | sed -n 's/.*"document":{"id":"\([^"]*\)".*/\1/p')
[ -n "$web" ] || fail "sin id del documento web: $added"
wait_ready 3
# La página cambia; la comprobación la hace un worker (se encola como la programada).
web_page "La dieta nacional sube a 60 euros para toda la plantilla." \
  | MSYS_NO_PATHCONV=1 compose exec -T backend sh -c 'cat > /tmp/web/viajes.html'
compose exec -T backend python - "$web" <<'PY'
import asyncio
import sys

from app.core.config import get_settings
from app.services.tasks import REFRESH_DOCUMENT, ArqTaskRunner


async def main() -> None:
    runner = await ArqTaskRunner.connect(get_settings().redis_url)
    await runner.enqueue(REFRESH_DOCUMENT, sys.argv[1], "smoke")
    await runner.close()


asyncio.run(main())
PY
for _ in $(seq 1 60); do
  compose logs --no-color worker | grep "Documento web actualizado" >/dev/null && break
  sleep 1
done
compose logs --no-color worker | grep "Documento web actualizado" >/dev/null \
  || fail "los workers no actualizaron el documento web"
wait_ready 3
curl -fsS -N -H 'Content-Type: application/json' \
  -d '{"message":"la dieta nacional sube a 60 euros"}' "$BASE/api/v1/chat" > "$WORK/chat4.txt"
grep -q 'http://backend:8099/viajes.html' "$WORK/chat4.txt" \
  || fail "la referencia no enlaza a la página: $(cat "$WORK/chat4.txt")"
# La página desaparece: «Comprobar ahora» lo anota.
MSYS_NO_PATHCONV=1 compose exec -T backend rm /tmp/web/viajes.html
admin -X POST "$BASE/api/v1/admin/documents/$web/refresh" | grep -q 'No encontrado (404)' \
  || fail "no se anotó la página no encontrada"

echo "Prueba de escalado superada."
