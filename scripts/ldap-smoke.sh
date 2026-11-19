#!/usr/bin/env bash
# Prueba del modo LDAP contra un OpenLDAP real (en Docker) con TLS (ldaps://).
# Uso: scripts/ldap-smoke.sh   (desde la raíz del repositorio)
set -euo pipefail

cd "$(dirname "$0")/.."
PROJECT=chatbot-rag-ldap
PORT=${SMOKE_PORT:-18090}
BASE="http://127.0.0.1:${PORT}"
WORK=$(mktemp -d)
WORK_HOST=$(cd "$WORK" && (pwd -W 2>/dev/null || pwd))
ENV_FILE="$WORK_HOST/ldap.env"

cat > "$ENV_FILE" <<ENV
CHATBOT_LLM_PROVIDER=fake
CHATBOT_EMBEDDING_PROVIDER=fake
CHATBOT_SESSION_SECRET=ldap-session-secret
CHATBOT_RATE_LIMIT_LOGIN_PER_MINUTE=0
CHATBOT_AUTH_MODE=ldap
CHATBOT_LDAP_URL=ldaps://ldap.ejemplo.org:636
CHATBOT_LDAP_CA_CERT_FILE=/data/ldap-ca.pem
CHATBOT_LDAP_BIND_DN=cn=admin,dc=ejemplo,dc=org
CHATBOT_LDAP_BIND_PASSWORD=admin-secreto
CHATBOT_LDAP_USER_BASE_DN=ou=people,dc=ejemplo,dc=org
CHATBOT_LDAP_ADMIN_GROUP_DN=cn=chatbot-admins,ou=groups,dc=ejemplo,dc=org
ENV

export CHATBOT_ENV_FILE="$ENV_FILE" CHATBOT_HTTP_PORT="$PORT" CHATBOT_HTTP_BIND=127.0.0.1
compose() {
  docker compose -p "$PROJECT" -f docker-compose.yml -f scripts/ldap/docker-compose.ldap.yml "$@"
}

cleanup() {
  status=$?
  if [ "$status" -ne 0 ]; then
    echo "--- Logs del backend ---"
    compose logs --no-color --tail 40 backend || true
  fi
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$status"
}
trap cleanup EXIT

fail() { echo "ERROR: $*" >&2; exit 1; }
step() { echo "==> $*"; }
code() { curl -s -o /dev/null -w '%{http_code}' "$@"; }
login() {  # login <cookies> <ruta> <usuario> <contraseña>
  code -c "$WORK/$1" -b "$WORK/$1" -H 'Content-Type: application/json' \
    -d "{\"username\":\"$3\",\"password\":\"$4\"}" "$BASE/api/v1/$2"
}

step "Generando una CA de prueba y el certificado de ldap.ejemplo.org"
mkdir -p "$WORK/certs"
# MSYS_NO_PATHCONV evita que Git Bash (Windows) convierta "/CN=..." en una ruta.
(
  cd "$WORK/certs"
  export MSYS_NO_PATHCONV=1
  openssl req -x509 -newkey rsa:2048 -nodes -days 2 -subj "/CN=Chatbot LDAP Test CA" \
    -addext "basicConstraints=critical,CA:TRUE" -addext "keyUsage=critical,keyCertSign,cRLSign" \
    -keyout ca.key -out ca.crt 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=ldap.ejemplo.org" \
    -keyout ldap.key -out ldap.csr 2>/dev/null
  printf '%s\n' "subjectAltName=DNS:ldap.ejemplo.org" "basicConstraints=CA:FALSE" \
    "keyUsage=critical,digitalSignature,keyEncipherment" "extendedKeyUsage=serverAuth" \
    "authorityKeyIdentifier=keyid,issuer" > ldap.ext
  openssl x509 -req -in ldap.csr -CA ca.crt -CAkey ca.key -CAcreateserial -days 2 \
    -extfile ldap.ext -out ldap.crt 2>/dev/null
  chmod 644 ./*
)
export LDAP_TEST_CERTS_DIR="$WORK_HOST/certs"

step "Arrancando OpenLDAP y la aplicación"
compose up -d --build --wait --wait-timeout 240

step "Sin la CA del directorio, el certificado no se acepta (503)"
[ "$(login c0 auth/session ana clave-ana)" = 503 ] || fail "se aceptó un certificado no verificado"

step "Con la CA instalada, el inicio de sesión funciona"
compose cp "$WORK_HOST/certs/ca.crt" backend:/data/ldap-ca.pem >/dev/null
[ "$(login ana auth/session ana clave-ana)" = 200 ] || fail "ana no pudo iniciar sesión"
curl -fsS -b "$WORK/ana" "$BASE/api/v1/auth/session" | grep -q '"display_name":"Ana López","is_admin":true' \
  || fail "datos de ana incorrectos: $(curl -s -b "$WORK/ana" "$BASE/api/v1/auth/session")"

step "Credenciales incorrectas e intentos de inyección"
[ "$(login x auth/session ana mala)" = 401 ] || fail "contraseña incorrecta aceptada"
[ "$(login x auth/session nadie x)" = 401 ] || fail "usuario inexistente aceptado"
[ "$(login x auth/session '*' clave-ana)" = 401 ] || fail "comodín aceptado"
[ "$(login x auth/session 'ana)(uid=*' clave-ana)" = 401 ] || fail "inyección aceptada"

step "Cada usuario ve solo sus conversaciones"
curl -fsS -b "$WORK/ana" -H 'Content-Type: application/json' -d '{"message":"Pregunta de Ana"}' \
  "$BASE/api/v1/chat" > "$WORK/chat.txt"
conversation=$(sed -n 's/.*"id": "\([^"]*\)".*/\1/p' "$WORK/chat.txt" | head -1)
[ "$(login luis auth/session luis clave-luis)" = 200 ] || fail "luis no pudo iniciar sesión"
[ "$(curl -fsS -b "$WORK/luis" "$BASE/api/v1/conversations")" = "[]" ] || fail "luis ve conversaciones ajenas"
[ "$(code -b "$WORK/luis" "$BASE/api/v1/conversations/$conversation/messages")" = 404 ] \
  || fail "luis accede a la conversación de ana"
curl -fsS -b "$WORK/ana" "$BASE/api/v1/conversations" | grep -q 'Pregunta de Ana' || fail "ana perdió su historial"
[ "$(code "$BASE/api/v1/conversations")" = 401 ] || fail "acceso sin sesión"

step "Administración según el grupo LDAP"
[ "$(login luisadm admin/session luis clave-luis)" = 403 ] || fail "luis no debería administrar"
[ "$(login anaadm admin/session ana clave-ana)" = 200 ] || fail "ana debería administrar"
[ "$(code -b "$WORK/anaadm" "$BASE/api/v1/admin/prompt")" = 200 ] || fail "ana no accede al prompt"
[ "$(code -b "$WORK/luis" "$BASE/api/v1/admin/prompt")" = 401 ] || fail "luis accede al prompt"

step "Si el directorio no responde, se informa sin bloquear las sesiones abiertas"
compose stop ldap >/dev/null 2>&1
[ "$(login x auth/session ana clave-ana)" = 503 ] || fail "se esperaba 503 sin directorio"
[ "$(code -b "$WORK/ana" "$BASE/api/v1/conversations")" = 200 ] || fail "se perdió la sesión abierta"

echo "Prueba LDAP superada."
