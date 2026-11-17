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

## API

Documentación interactiva en http://localhost:8000/docs. Endpoints principales:

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/health` | Estado de la API y de la BD |
| GET/POST | `/api/v1/conversations` | Listar / crear conversaciones |
| PATCH/DELETE | `/api/v1/conversations/{id}` | Renombrar / eliminar |
| GET | `/api/v1/conversations/{id}/messages` | Mensajes de una conversación |
| POST | `/api/v1/chat` | Envía un mensaje; respuesta en streaming SSE (`conversation`, `token`, `done`, `error`) |

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
