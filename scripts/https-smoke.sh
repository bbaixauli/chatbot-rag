#!/usr/bin/env bash
# Prueba de humo del despliegue con HTTPS (docker-compose.https.yml): construye las imágenes,
# arranca Caddy delante de la aplicación y comprueba los dos modos de certificado
# (autofirmado con la CA interna de Caddy y certificado propio), la redirección a HTTPS, el
# streaming del chat a través de los dos proxies, la cookie segura y la IP real del cliente.
# Uso: scripts/https-smoke.sh   (desde la raíz del repositorio)
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT=chatbot-rag-https-smoke
HTTPS_PORT=${SMOKE_HTTPS_PORT:-18443}
HTTP_PORT=${SMOKE_HTTP_PORT:-18081}
DOMAIN=localhost
BASE="https://${DOMAIN}:${HTTPS_PORT}"
WORK=$(mktemp -d)
# Rutas de WORK para Docker: en Git Bash (Windows) deben tener formato de Windows.
WORK_HOST=$(cd "$WORK" && (pwd -W 2>/dev/null || pwd))
ENV_FILE="$WORK_HOST/smoke.env"
COOKIES="$WORK/cookies.txt"
CERTS="$WORK/certs"
mkdir -p "$CERTS"

cat > "$ENV_FILE" <<ENV
CHATBOT_LLM_PROVIDER=fake
CHATBOT_EMBEDDING_PROVIDER=fake
CHATBOT_ADMIN_PASSWORD=smoke-secreto
CHATBOT_SESSION_SECRET=smoke-session-secret
ENV

export CHATBOT_ENV_FILE="$ENV_FILE" CHATBOT_HTTP_BIND=127.0.0.1 CHATBOT_DOMAIN="$DOMAIN" \
  CHATBOT_HTTPS_PORT="$HTTPS_PORT" CHATBOT_HTTP_REDIRECT_PORT="$HTTP_PORT" \
  CHATBOT_CERTS_DIR="$WORK_HOST/certs" CHATBOT_TLS=interno
compose() { docker compose -p "$PROJECT" -f docker-compose.yml -f docker-compose.https.yml "$@"; }

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

# curl con la CA indicada (sin -k: se comprueba de verdad el certificado). En Windows (Schannel)
# no hay listas de revocación para estas CA de prueba.
CURL_OPTS=(--resolve "${DOMAIN}:${HTTPS_PORT}:127.0.0.1")
curl -V | grep -q Schannel && CURL_OPTS+=(--ssl-no-revoke)
CA="$WORK/root.crt"
https() { curl -fsS "${CURL_OPTS[@]}" --cacert "$CA" "$@"; }

# Espera a que el certificado servido por Caddy sea válido para la CA de $CA.
wait_tls() {
  for _ in $(seq 1 30); do
    https -o /dev/null "$BASE/api/v1/health" 2>/dev/null && return 0
    sleep 1
  done
  https -o /dev/null "$BASE/api/v1/health" || fail "certificado no válido para $1"
}

step "Construyendo y arrancando (certificado autofirmado: CHATBOT_TLS=interno)"
compose up -d --build --wait --wait-timeout 180

step "La CA interna de Caddy se puede extraer para instalarla en los equipos"
for _ in $(seq 1 30); do
  compose cp caddy:/data/caddy/pki/authorities/local/root.crt "$CA" >/dev/null 2>&1 && break
  sleep 1
done
[ -s "$CA" ] || fail "no se encontró la CA raíz de Caddy"
wait_tls "la CA interna"

step "HTTP redirige a HTTPS (con el puerto configurado)"
redirect=$(curl -sS -o /dev/null -w '%{http_code} %{redirect_url}' \
  --resolve "${DOMAIN}:${HTTP_PORT}:127.0.0.1" "http://${DOMAIN}:${HTTP_PORT}/admin")
[ "$redirect" = "308 ${BASE}/admin" ] || fail "redirección inesperada: $redirect"

step "La SPA se sirve por HTTPS con HSTS"
https -D "$WORK/index.headers" "$BASE/" | grep -q '<div id="root">' || fail "index.html no servido"
grep -qi '^strict-transport-security: max-age=31536000' "$WORK/index.headers" || fail "falta HSTS"
https "$BASE/admin/documents" | grep -q '<div id="root">' || fail "sin fallback de la SPA"

step "El chat llega en streaming a través de Caddy y nginx (sin búfer)"
# El modelo simulado repite la pregunta palabra a palabra (10 ms cada una): con búfer, el
# primer byte llegaría al final.
question=$(printf 'palabra%.0s ' $(seq 1 150))
timing=$(https -N -o "$WORK/chat.txt" -w '%{time_starttransfer} %{time_total}' \
  -H 'Content-Type: application/json' -d "{\"message\":\"${question% }\"}" "$BASE/api/v1/chat")
grep -q 'event: done' "$WORK/chat.txt" || fail "el chat no terminó: $(cat "$WORK/chat.txt")"
echo "    primer byte y total (s): $timing"
awk -v t="$timing" 'BEGIN { split(t, a, " "); exit !(a[2] - a[1] >= 0.5) }' \
  || fail "la respuesta llegó de golpe (¿búfer en el proxy?): $timing"

step "La cookie de sesión es segura"
https -D "$WORK/login.headers" -c "$COOKIES" -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"smoke-secreto"}' "$BASE/api/v1/admin/session" >/dev/null
grep -i '^set-cookie: chatbot_session=' "$WORK/login.headers" | grep -qi '; *secure' \
  || fail "la cookie de sesión no es Secure: $(grep -i '^set-cookie' "$WORK/login.headers")"

step "Se admite la subida de un documento grande"
{
  echo '# Documento grande'
  for i in $(seq 1 20000); do
    printf '\nPárrafo %d. Las vacaciones se solicitan con quince días de antelación al responsable del servicio, que las aprueba según las necesidades del servicio y el calendario laboral vigente.\n' "$i"
  done
} > "$WORK/grande.md"
upload=$(https -o /dev/null -w '%{http_code}' -b "$COOKIES" -F "files=@$WORK/grande.md" \
  "$BASE/api/v1/admin/documents")
case "$upload" in 2??) ;; *) fail "subida rechazada: $upload" ;; esac
echo "    $(wc -c < "$WORK/grande.md") bytes subidos"

step "El backend recibe la IP real del cliente y no la falsificada"
caddy_ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' \
  "$(compose ps -q caddy)")
https -o /dev/null -H 'X-Forwarded-For: 203.0.113.7' "$BASE/api/v1/conversations"
logs=$(compose logs --no-color backend)
client=$(echo "$logs" | grep '"path": "/api/v1/conversations"' | tail -1 \
  | sed -n 's/.*"client": "\([^"]*\)".*/\1/p')
echo "    IP del cliente: ${client:-?} (Caddy: $caddy_ip)"
[ -n "$client" ] || fail "no se encontró la petición en el log"
[ "$client" != "203.0.113.7" ] || fail "se aceptó la IP falsificada"
[ "$client" != "$caddy_ip" ] || fail "el backend ve la IP de Caddy, no la del cliente"

# --- Certificado propio -------------------------------------------------------------------

# Crea una CA de prueba y un certificado para $DOMAIN firmado por ella, en el directorio de
# certificados (cert.pem con la cadena completa y key.pem), como haría la organización.
issue_certificate() {  # issue_certificate <nombre de la CA>
  (
    cd "$CERTS"
    export MSYS_NO_PATHCONV=1
    openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=$1" \
      -keyout ca.key -out ca.pem 2>/dev/null
    openssl req -newkey rsa:2048 -nodes -subj "/CN=${DOMAIN}" \
      -keyout key.pem -out server.csr 2>/dev/null
    printf 'subjectAltName=DNS:%s\nextendedKeyUsage=serverAuth\n' "$DOMAIN" > server.ext
    openssl x509 -req -in server.csr -CA ca.pem -CAkey ca.key -CAcreateserial -days 2 \
      -extfile server.ext -out server.pem 2>/dev/null
    cat server.pem ca.pem > cert.pem
    # Caddy se ejecuta como root, pero en Linux el montaje conserva los permisos.
    chmod 644 key.pem cert.pem
  )
}

step "Certificado de la organización (CHATBOT_TLS=certificado)"
issue_certificate "CA de prueba 1"
cp "$CERTS/ca.pem" "$WORK/ca1.pem"
export CHATBOT_TLS=certificado
compose up -d --wait --wait-timeout 60 caddy
CA="$WORK/ca1.pem"
wait_tls "el certificado propio"
https "$BASE/" | grep -q '<div id="root">' || fail "index.html no servido con el certificado propio"

step "Renovación: se sustituyen los ficheros y se recarga Caddy"
issue_certificate "CA de prueba 2"
cp "$CERTS/ca.pem" "$WORK/ca2.pem"
reload=$(MSYS_NO_PATHCONV=1 compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --force 2>&1) \
  || fail "no se pudo recargar Caddy: $reload"
CA="$WORK/ca2.pem"
wait_tls "el certificado renovado"
CA="$WORK/ca1.pem"
https -o /dev/null "$BASE/api/v1/health" 2>/dev/null && fail "se sigue sirviendo el certificado anterior"

echo "Prueba de humo superada."
