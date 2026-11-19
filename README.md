# Chatbot RAG

Chat conectado a un modelo de IA compatible con la API de OpenAI, con RAG sobre documentos
(PDF, Markdown y HTML) y referencias a los ficheros de origen. Incluye una zona de
administración para definir el prompt de sistema, el mensaje inicial del chat y gestionar los
documentos.

Desarrollado utilizando Vibe Coding con Claude Code.

## Estructura

```
.github/workflows/   CI de GitHub Actions (backend, frontend, E2E y Docker)
backend/
  app/api/           Endpoints (chat, conversaciones, documentos, administración)
  app/services/      Lógica: chat, RAG, embeddings, índice vectorial, ingesta…
  app/db/            Modelos SQLAlchemy y sesión
  alembic/           Migraciones de la base de datos
  tests/             Tests de pytest
frontend/
  src/               SPA React + TypeScript (Vite, Tailwind)
  e2e/               Tests de interfaz con Playwright
  docker/            Configuración de nginx para la imagen
docs/                Guía de despliegue
scripts/             Prueba de humo del despliegue con Docker
docker-compose.yml   Despliegue con Docker (y docker-compose.escalado.yml: Qdrant, Redis y workers)
data/                Datos locales: BD, índice y ficheros subidos (no versionado)
```

## Requisitos

- [uv](https://docs.astral.sh/uv/) (gestiona Python 3.12 automáticamente)
- Node.js LTS (≥ 20.19)

## Despliegue con Docker

```bash
cp .env.example .env      # configura el servidor de IA y la contraseña de administración
docker compose up -d --build
```

La aplicación queda en http://localhost:8080. La [guía de despliegue](docs/despliegue.md)
explica la configuración, HTTPS, actualizaciones, logs y copias de seguridad
(`docker compose exec backend python -m app.backup create`).

## Puesta en marcha en local

```bash
# Backend (puerto 8000)
cd backend
cp ../.env.example .env
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload

# Frontend (puerto 5173, redirige /api al backend)
cd frontend
npm install
npm run dev
```

Abre http://localhost:5173.

Configura en `backend/.env` la URL y el modelo del servidor de IA (`CHATBOT_LLM_*`). Para
probar la interfaz sin servidor de IA, usa `CHATBOT_LLM_PROVIDER=fake`.

La administración (http://localhost:5173/admin) se habilita definiendo
`CHATBOT_ADMIN_PASSWORD` (y, recomendado, `CHATBOT_SESSION_SECRET`, de al menos 16 caracteres;
en producción es obligatorio y no se admite la contraseña de ejemplo). Desde ella se edita el
prompt de sistema, que se guarda con histórico de versiones y se aplica a los mensajes nuevos,
se configura el inicio del chat y se gestionan los documentos del RAG (PDF, Markdown y HTML).

### Usuarios

Por defecto el chat es anónimo. Con `CHATBOT_AUTH_MODE=ldap` cada usuario inicia sesión con su
cuenta del directorio (OpenLDAP o Active Directory) y solo ve sus conversaciones; los miembros
de `CHATBOT_LDAP_ADMIN_GROUP_DN` administran la aplicación. Configuración y ejemplos en la
[guía de despliegue](docs/despliegue.md#usuarios-con-ldap).

### Mensaje inicial y preguntas sugeridas

En *Administración → Mensaje inicial* se puede definir:

- Un **mensaje inicial** (admite Markdown) que aparece como primer mensaje del asistente en cada
  conversación. Es solo de presentación: no se guarda en la conversación ni se envía al modelo.
  Si se deja vacío, el chat muestra la portada habitual.
- Hasta **5 preguntas sugeridas**, que aparecen sobre el campo de texto al empezar una
  conversación. Al pulsar una, su texto sustituye al que hubiera en el campo y se puede editar
  antes de enviarlo.

### Embeddings

Los documentos se trocean y se indexan en ChromaDB (`data/chroma`) con el modelo de embeddings
configurado en `CHATBOT_EMBEDDING_*`:

- `openai` (por defecto): usa el endpoint `/v1/embeddings` del proxy LiteLLM (misma URL que el
  chat salvo que se indique `CHATBOT_EMBEDDING_BASE_URL`). `CHATBOT_EMBEDDING_MODEL` debe coincidir
  con el `model_name` configurado en LiteLLM.
- `local`: calcula los embeddings en el propio servidor con sentence-transformers
  (`uv sync --extra local-embeddings`; descarga el modelo la primera vez).
- `fake`: simulado, sin red (tests y demos).

Si se cambia de modelo, pulsa **Reindexar todo** en *Administración → Documentos* (vacía el
índice y vuelve a indexar todos los documentos; mientras tanto, el chat responde sin ellos).

Para volúmenes grandes, el índice puede estar en **Qdrant** y la indexación repartirse entre
**workers** con una cola en **Redis** (`docker-compose.escalado.yml`, ver la
[guía de despliegue](docs/despliegue.md#escalado-qdrant-y-workers)).

### Respuestas con referencias

En cada pregunta se recuperan los fragmentos más parecidos (`CHATBOT_RAG_TOP_K`) por encima de
`CHATBOT_RAG_MIN_SCORE`, se añaden numerados al prompt de sistema y se pide al modelo que cite
con `[n]`. Bajo la respuesta se muestran las fuentes citadas (fichero y página o sección) con
enlace al original; los PDF se abren en la página citada. Si el modelo no cita, se muestran
como «Fuentes consultadas».

Las preguntas de seguimiento («¿y con cuánta antelación?») se reformulan con el historial antes
de buscar (`CHATBOT_RAG_CONDENSE_QUESTION`); la respuesta se genera con la pregunta original.

### Calibrar la búsqueda con un conjunto de evaluación

El umbral de similitud, el top-k y el tamaño de fragmento adecuados dependen del modelo de
embeddings y de los documentos. El script de evaluación los mide con preguntas reales:

```bash
cd backend
# Con los documentos de una carpeta…
uv run python -m app.evaluation --dataset eval/ejemplo/preguntas.json \
    --documents eval/ejemplo/documentos \
    --chunk-sizes 1000,2000,3000 --overlaps 200,400 --top-k 3,5,8
# …o con los documentos ya subidos a la aplicación (omitir --documents)
uv run python -m app.evaluation --dataset mis-preguntas.json --output informe.json
```

Usa los modelos configurados en `.env` (p. ej. el de embeddings de LiteLLM). El conjunto es un
JSON con preguntas y la fuente esperada (`fichero` y, opcionalmente, `pagina` o `seccion`);
las preguntas con `"esperado": []` no deberían recuperar nada, y las que tienen `historial`
prueban la reformulación con `--condensar`. Ver [`backend/eval/ejemplo`](backend/eval/ejemplo).

El informe muestra, por configuración, el acierto@k, MRR, recall, precisión y falsos positivos,
y recomienda los valores de `CHATBOT_CHUNK_*`, `CHATBOT_RAG_TOP_K` y `CHATBOT_RAG_MIN_SCORE`.
Tras cambiar el troceado hay que reindexar los documentos.

## API

Documentación interactiva en http://localhost:8000/docs. Endpoints principales:

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/health` | Estado de la API y de la BD |
| GET/POST | `/api/v1/conversations` | Listar / crear conversaciones |
| PATCH/DELETE | `/api/v1/conversations/{id}` | Renombrar / eliminar |
| GET | `/api/v1/conversations/{id}/messages` | Mensajes de una conversación |
| GET | `/api/v1/chat/config` | Mensaje inicial y preguntas sugeridas |
| POST | `/api/v1/chat` | Envía un mensaje; respuesta en streaming SSE (`conversation`, `token`, `sources`, `done`, `error`) |
| GET/POST/DELETE | `/api/v1/auth/session` | Sesión del usuario del chat (modo LDAP) |
| GET/POST/DELETE | `/api/v1/admin/session` | Estado / inicio / cierre de sesión de administración |
| GET/PUT | `/api/v1/admin/chat-config` | Mensaje inicial y preguntas sugeridas (admin) |
| GET/PUT | `/api/v1/admin/prompt` | Prompt de sistema vigente / guardar nueva versión (admin) |
| GET | `/api/v1/admin/prompt/versions` | Historial de versiones (admin) |
| POST | `/api/v1/admin/prompt/versions/{id}/restore` | Restaurar una versión (admin) |
| GET/POST | `/api/v1/admin/documents` | Listar / subir documentos (multipart, varios ficheros) (admin) |
| GET | `/api/v1/admin/documents/stats` | Nº de documentos, fragmentos y modelo de embeddings (admin) |
| POST | `/api/v1/admin/documents/reindex` | Reindexar todos (admin) |
| GET/DELETE | `/api/v1/admin/documents/{id}` | Detalle / eliminar (admin) |
| POST | `/api/v1/admin/documents/{id}/reindex` | Reindexar uno (admin) |
| GET | `/api/v1/admin/documents/{id}/file` | Descargar el original (admin) |
| GET | `/api/v1/documents/{id}/file` | Ver el original desde una referencia del chat (PDF en línea, Markdown como texto, HTML en sandbox) |

## Calidad y tests

| | Backend (`backend/`) | Frontend (`frontend/`) |
|---|---|---|
| Formato | `uv run ruff format .` | `npm run format` |
| Lint | `uv run ruff check .` | `npm run lint` |
| Tipos | `uv run mypy app tests` | `npm run typecheck` |
| Tests unitarios | `uv run pytest` (cobertura ≥ 85 %) | `npm test` (cobertura ≥ 80 %) |
| Tests de interfaz | — | `npm run test:e2e` (Playwright) |
| Despliegue | `scripts/docker-smoke.sh`, `ldap-smoke.sh` y `escalado-smoke.sh` (desde la raíz; requieren Docker) | |

Los tests E2E levantan automáticamente backend (puerto 8001, con modelo simulado) y frontend
(puerto 5174) con una carpeta de datos temporal. La primera vez instala el navegador con
`npx playwright install chromium`. Los tests que cambian la configuración global del chat se
ejecutan en un proyecto aparte, después del resto. Si en un equipo lento fallan por tiempo de
espera al ejecutarse en paralelo, prueba con `npm run test:e2e -- --workers=1`.

El CI de GitHub Actions ejecuta todo lo anterior en cada pull request.

El backend registra cada petición con un `X-Request-ID` (`CHATBOT_LOG_FORMAT=json` para logs
estructurados) y limita las peticiones por IP al chat y al inicio de sesión
(`CHATBOT_RATE_LIMIT_*`).

> **Windows:** ejecuta Vitest desde una ruta con la letra de unidad en mayúscula
> (`F:\...`). Si el terminal se abre en `f:\...`, Vitest carga los módulos dos veces y
> falla con *"Vitest failed to find the current suite"*.
