# Despliegue con Docker

Guía para instalar, actualizar y mantener el chatbot en un servidor con Docker.

## Arquitectura

```
navegador ──► frontend (nginx :8080) ──► /api/* ──► backend (FastAPI :8000)
                │                                     │
                └ SPA compilada                       └ volumen chatbot-data (/data)
                                                        ├ app.db       base de datos SQLite
                                                        ├ uploads/     documentos originales
                                                        ├ chroma/      índice vectorial
                                                        └ backups/     copias de seguridad
```

- **frontend**: nginx sirve la aplicación web y redirige `/api` al backend. Es el único
  contenedor que publica un puerto.
- **backend**: API, indexación de documentos y acceso al modelo. Al arrancar aplica las
  migraciones de la base de datos y reanuda las indexaciones pendientes.
- El servidor de IA (LiteLLM, vLLM, Ollama…) es externo y se configura con `CHATBOT_LLM_*` y
  `CHATBOT_EMBEDDING_*`.

> El backend debe ejecutarse con **una sola réplica**: la cola de indexación y el límite de
> peticiones se guardan en memoria.

## Requisitos

- Docker Engine 24 o superior con el plugin `docker compose` (v2.24+).
- Acceso desde el servidor al servidor de IA.
- 2 GB de RAM como mínimo (más si se usan embeddings locales).

## Instalación

```bash
git clone https://github.com/bbaixauli/chatbot-rag.git
cd chatbot-rag
cp .env.example .env
```

Edita `.env` y revisa como mínimo:

| Variable | Valor |
|---|---|
| `CHATBOT_LLM_BASE_URL`, `CHATBOT_LLM_API_KEY`, `CHATBOT_LLM_MODEL` | Servidor de IA y modelo de chat |
| `CHATBOT_EMBEDDING_MODEL` (y `CHATBOT_EMBEDDING_BASE_URL` si es otro servidor) | Modelo de embeddings |
| `CHATBOT_ADMIN_PASSWORD` | Contraseña de la administración |
| `CHATBOT_SESSION_SECRET` | Clave aleatoria: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
| `CHATBOT_SESSION_COOKIE_SECURE` | `true` si se accede por HTTPS (recomendado) |

`CHATBOT_DATA_DIR` se ignora en Docker: los datos siempre están en el volumen `/data`.

Arranca:

```bash
docker compose up -d --build
docker compose ps        # ambos servicios deben aparecer como "healthy"
```

La aplicación queda en `http://<servidor>:8080` y la administración en `/admin`.

### Opciones del compose

Se definen como variables de entorno al ejecutar `docker compose` o en el propio `.env`:

| Variable | Por defecto | Uso |
|---|---|---|
| `CHATBOT_HTTP_PORT` | `8080` | Puerto publicado |
| `CHATBOT_HTTP_BIND` | `0.0.0.0` | Interfaz; usa `127.0.0.1` si hay un proxy HTTPS en el mismo servidor |
| `CHATBOT_MAX_BODY_SIZE` | `60m` | Tamaño máximo de una petición en nginx (debe superar `CHATBOT_UPLOAD_MAX_MB`) |
| `CHATBOT_UV_EXTRAS` | — | `local-embeddings` para incluir sentence-transformers en la imagen |
| `CHATBOT_IMAGE_TAG` | `latest` | Etiqueta de las imágenes construidas |

Con `CHATBOT_EMBEDDING_PROVIDER=local`, el modelo se descarga la primera vez en
`/data/modelos` y se conserva entre reinicios.

## Usuarios con LDAP

Por defecto (`CHATBOT_AUTH_MODE=none`) el chat es anónimo: todas las conversaciones forman un
único historial compartido y la administración usa la cuenta local
(`CHATBOT_ADMIN_USERNAME`/`CHATBOT_ADMIN_PASSWORD`).

Con `CHATBOT_AUTH_MODE=ldap`:

- Cada usuario inicia sesión con su cuenta del directorio y **solo ve sus conversaciones**.
- Los miembros del grupo `CHATBOT_LDAP_ADMIN_GROUP_DN` acceden a la administración con su
  usuario. Las versiones del prompt registran quién las guardó.
- La cuenta local de administración sigue funcionando si está definida: sirve como acceso de
  emergencia cuando el directorio no está disponible. Sus credenciales nunca se envían al
  directorio. Si no se necesita, deja `CHATBOT_ADMIN_PASSWORD` vacío.

El backend busca al usuario con la cuenta de servicio, comprueba la contraseña haciendo *bind*
con su DN y, después, comprueba la pertenencia al grupo de administración. Los valores
introducidos se escapan antes de construir los filtros LDAP.

**OpenLDAP**

```dotenv
CHATBOT_AUTH_MODE=ldap
CHATBOT_LDAP_URL=ldaps://ldap.ejemplo.org:636
CHATBOT_LDAP_BIND_DN=cn=chatbot,ou=services,dc=ejemplo,dc=org
CHATBOT_LDAP_BIND_PASSWORD=...
CHATBOT_LDAP_USER_BASE_DN=ou=people,dc=ejemplo,dc=org
CHATBOT_LDAP_USER_FILTER=(uid={username})
CHATBOT_LDAP_USER_ID_ATTRIBUTE=uid
CHATBOT_LDAP_DISPLAY_NAME_ATTRIBUTE=cn
# groupOfNames, groupOfUniqueNames o posixGroup
CHATBOT_LDAP_ADMIN_GROUP_DN=cn=chatbot-admins,ou=groups,dc=ejemplo,dc=org
```

**Active Directory**

```dotenv
CHATBOT_AUTH_MODE=ldap
CHATBOT_LDAP_URL=ldaps://dc01.empresa.local:636
CHATBOT_LDAP_BIND_DN=CN=svc-chatbot,OU=Servicios,DC=empresa,DC=local
CHATBOT_LDAP_BIND_PASSWORD=...
CHATBOT_LDAP_USER_BASE_DN=OU=Usuarios,DC=empresa,DC=local
CHATBOT_LDAP_USER_FILTER=(&(objectClass=user)(sAMAccountName={username}))
CHATBOT_LDAP_USER_ID_ATTRIBUTE=sAMAccountName
CHATBOT_LDAP_DISPLAY_NAME_ATTRIBUTE=displayName
CHATBOT_LDAP_ADMIN_GROUP_DN=CN=Chatbot-Admins,OU=Grupos,DC=empresa,DC=local
CHATBOT_LDAP_NESTED_GROUPS=true
```

Notas:

- **TLS**: usa `ldaps://` o `CHATBOT_LDAP_START_TLS=true`; el certificado del servidor se
  verifica siempre. Si la CA es interna, copia su certificado al volumen (por ejemplo
  `docker compose cp ca.pem backend:/data/ldap-ca.pem`) y define
  `CHATBOT_LDAP_CA_CERT_FILE=/data/ldap-ca.pem`.
- El identificador del usuario es el valor de `CHATBOT_LDAP_USER_ID_ATTRIBUTE` en minúsculas.
  Si se cambia ese atributo, los usuarios dejarán de ver sus conversaciones anteriores.
- Los permisos se leen al iniciar sesión: si se añade o quita a alguien del grupo de
  administración, el cambio se aplica en su siguiente inicio de sesión (la sesión dura
  `CHATBOT_SESSION_MAX_AGE_SECONDS`, 8 horas por defecto).
- Las conversaciones creadas en modo anónimo pertenecen al usuario `anonymous` y no aparecen
  en el historial de nadie al activar LDAP.
- Si el directorio no responde, el inicio de sesión muestra un aviso y el log del backend
  registra el motivo. Las sesiones ya iniciadas siguen funcionando.
- Es imprescindible definir `CHATBOT_SESSION_SECRET`; con HTTPS, también
  `CHATBOT_SESSION_COOKIE_SECURE=true`.

## HTTPS

El contenedor sirve HTTP. Para HTTPS, coloca delante el proxy inverso de la organización
(nginx, Traefik, Caddy…) apuntando a `http://127.0.0.1:8080`, con `CHATBOT_HTTP_BIND=127.0.0.1`
y `CHATBOT_SESSION_COOKIE_SECURE=true`.

El proxy debe:

- No almacenar en búfer las respuestas de `/api/v1/chat` (streaming SSE). En nginx:
  `proxy_buffering off;` y `proxy_read_timeout 300s;`.
- Permitir subidas del tamaño configurado (`client_max_body_size` en nginx).

**IP real de los clientes.** El límite de peticiones se aplica por IP. Por seguridad, el nginx
del contenedor ignora la cabecera `X-Forwarded-For` que reciba y usa la IP de quien le conecta:
detrás de otro proxy, todas las peticiones parecerían venir de él. Para conservar la IP real,
añade en `frontend/docker/default.conf.template`, dentro de `server`, el módulo `real_ip` con la
IP de tu proxy y reconstruye la imagen:

```nginx
set_real_ip_from 10.0.0.5;      # IP del proxy inverso
real_ip_header X-Forwarded-For;
```

## Actualización

```bash
git pull
docker compose up -d --build
```

Las migraciones de la base de datos se aplican solas al arrancar el backend. Haz antes una copia
de seguridad (ver abajo). Si una versión cambia el modelo de embeddings o el troceado, pulsa
**Reindexar todo** en *Administración → Documentos*.

## Logs

```bash
docker compose logs -f backend
docker compose logs -f frontend
```

El backend escribe una línea JSON por evento (`CHATBOT_LOG_FORMAT=json`), apta para Loki, ELK u
otros agregadores. Cada petición se registra con método, ruta, estado y duración, y lleva un
`request_id` que también aparece en la cabecera `X-Request-ID` de la respuesta: sirve para
localizar en el log los errores que reporte un usuario. Nunca se registra el contenido de los
mensajes.

Docker rota los logs según su configuración (`/etc/docker/daemon.json`). Por ejemplo:

```json
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
```

## Errores del modelo y límite de peticiones

- Si el servidor de IA falla, el chat muestra un mensaje según la causa (tiempo de espera, sin
  conexión, saturado, credenciales, conversación demasiado larga) y el detalle técnico queda en
  el log del backend. La pregunta del usuario se conserva en el historial.
- Si el servicio de embeddings no responde, el chat sigue funcionando sin documentación.
- Límite por IP y minuto, configurable (`0` lo desactiva):
  `CHATBOT_RATE_LIMIT_CHAT_PER_MINUTE` (20) y `CHATBOT_RATE_LIMIT_LOGIN_PER_MINUTE` (5). Al
  superarlo, la API responde `429` con la cabecera `Retry-After`.

## Copias de seguridad

Una copia incluye la base de datos (conversaciones, prompts, configuración y documentos) y los
ficheros originales subidos. El índice vectorial no se incluye: se reconstruye a partir de los
documentos al restaurar.

### Crear una copia

Se puede hacer con la aplicación en marcha (usa la API de backup de SQLite):

```bash
docker compose exec backend python -m app.backup create --keep 7
```

Crea `/data/backups/chatbot-backup-<fecha>.tar.gz` y conserva las 7 más recientes. Para sacarla
del servidor:

```bash
docker compose cp backend:/data/backups ./backups
```

**Copia diaria** con cron en el servidor (a las 3:00, guardando una copia fuera del volumen):

```cron
0 3 * * * cd /opt/chatbot-rag && docker compose exec -T backend python -m app.backup create --keep 7 && docker compose cp backend:/data/backups /srv/copias/chatbot
```

Guarda las copias también en otro equipo o almacenamiento: el volumen de Docker no protege ante
un fallo del disco.

### Restaurar una copia

Con la aplicación parada, usando un contenedor temporal sobre el mismo volumen:

```bash
docker compose stop backend
docker compose run --rm -v "$PWD/backups:/restaurar:ro" --entrypoint python backend \
  -m app.backup restore /restaurar/chatbot-backup-20261118-030000.tar.gz --force
docker compose start backend
```

`--force` sustituye la base de datos y los documentos actuales. Al arrancar, el backend reindexa
todos los documentos (en *Administración → Documentos* se ve el progreso). Mientras tanto, el
chat responde sin documentación.

### Instalación nueva a partir de una copia

```bash
docker compose up -d --no-start      # crea el volumen y las imágenes
docker compose run --rm -v "$PWD/backups:/restaurar:ro" --entrypoint python backend \
  -m app.backup restore /restaurar/chatbot-backup-20261118-030000.tar.gz
docker compose up -d
```

## Comprobación del despliegue

`scripts/docker-smoke.sh` construye las imágenes, arranca un entorno aislado con modelos
simulados (puerto 18080) y comprueba el recorrido principal: aplicación web, API, chat en
streaming, subida e indexación de un documento, límite de peticiones, copia de seguridad,
persistencia de los datos y formato de los logs. Al terminar elimina contenedores y volúmenes.
Se ejecuta también en el CI.

## Solución de problemas

| Síntoma | Causa probable |
|---|---|
| `backend` no llega a *healthy* | Revisa `docker compose logs backend`: variables mal escritas o migración fallida |
| La respuesta del chat aparece de golpe al final | Un proxy intermedio almacena en búfer el SSE (`proxy_buffering off`) |
| `413 Request Entity Too Large` al subir | Aumenta `CHATBOT_MAX_BODY_SIZE` (y `client_max_body_size` en el proxy externo) |
| «No se pudo conectar con el modelo» | El contenedor no alcanza `CHATBOT_LLM_BASE_URL`. Si el servidor de IA está en el mismo equipo, usa su IP o `host.docker.internal`, no `localhost` |
| Las sesiones de administración se pierden al reiniciar | Falta `CHATBOT_SESSION_SECRET` |
| Todos los usuarios reciben `429` a la vez | Hay un proxy delante sin configurar `real_ip` (ver HTTPS) |
