# Chatbot RAG

Chat conectado a un modelo de IA compatible con la API de OpenAI, con RAG sobre documentos
(PDF, Markdown y HTML) y referencias a los ficheros de origen. Incluye una zona de
administración para definir el prompt de sistema y gestionar los documentos.

## Estructura

```
backend/    API FastAPI (Python 3.12, uv, SQLAlchemy, Alembic)
frontend/   SPA React + TypeScript (Vite, Tailwind, Vitest, Playwright)
data/       Datos locales: BD, índice y ficheros subidos (no versionado)
```

## Requisitos

- [uv](https://docs.astral.sh/uv/) (gestiona Python 3.12 automáticamente)
- Node.js LTS (≥ 20.19)

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
`CHATBOT_ADMIN_PASSWORD` (y, recomendado, `CHATBOT_SESSION_SECRET`). Desde ella se edita el
prompt de sistema, que se guarda con histórico de versiones y se aplica a los mensajes nuevos,
y se gestionan los documentos del RAG (PDF, Markdown y HTML).

### Embeddings

Los documentos se trocean y se indexan en ChromaDB (`data/chroma`) con el modelo de embeddings
configurado en `CHATBOT_EMBEDDING_*`:

- `openai` (por defecto): usa el endpoint `/v1/embeddings` del proxy LiteLLM (misma URL que el
  chat salvo que se indique `CHATBOT_EMBEDDING_BASE_URL`). `CHATBOT_EMBEDDING_MODEL` debe coincidir
  con el `model_name` configurado en LiteLLM.
- `local`: calcula los embeddings en el propio servidor con sentence-transformers
  (`uv sync --extra local-embeddings`; descarga el modelo la primera vez).
- `fake`: simulado, sin red (tests y demos).

Si se cambia de modelo, pulsa **Reindexar todo** en *Administración → Documentos*.

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
| POST | `/api/v1/chat` | Envía un mensaje; respuesta en streaming SSE (`conversation`, `token`, `sources`, `done`, `error`) |
| GET/POST/DELETE | `/api/v1/admin/session` | Estado / inicio / cierre de sesión de administración |
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

Los tests E2E levantan automáticamente backend (puerto 8001, con modelo simulado) y frontend
(puerto 5174) con una carpeta de datos temporal. La primera vez instala el navegador con
`npx playwright install chromium`.

El CI de GitHub Actions ejecuta todo lo anterior en cada pull request.

> **Windows:** ejecuta Vitest desde una ruta con la letra de unidad en mayúscula
> (`F:\...`). Si el terminal se abre en `f:\...`, Vitest carga los módulos dos veces y
> falla con *"Vitest failed to find the current suite"*.
