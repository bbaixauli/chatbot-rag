# Chatbot RAG

Chat conectado a un modelo de IA compatible con la API de OpenAI, con RAG sobre documentos
(PDF, Markdown, HTML, Word, PowerPoint, Excel y OpenDocument) y referencias a los ficheros de
origen. Incluye una zona de
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
scripts/             Pruebas de humo del despliegue con Docker
docker-compose.yml   Despliegue con Docker (y docker-compose.escalado.yml: Qdrant, Redis y workers;
                     docker-compose.https.yml: HTTPS con Caddy;
                     docker-compose.postgres.yml: base de datos PostgreSQL)
deploy/caddy/        Configuración de Caddy (proxy HTTPS)
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

La aplicación queda en http://localhost:8080. Para servirla por HTTPS (certificado autofirmado o
de la organización), añade `docker-compose.https.yml`, que incluye Caddy como proxy. La base de
datos es SQLite por defecto; para usar PostgreSQL, en un contenedor (`docker-compose.postgres.yml`)
o en un servidor de la organización, se configuran `CHATBOT_DATABASE_URL` y
`CHATBOT_DATABASE_PASSWORD`. La
[guía de despliegue](docs/despliegue.md) explica la configuración, HTTPS, PostgreSQL, actualizaciones, logs
y copias de seguridad (`docker compose exec backend python -m app.backup create`).

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

### Configuración

La configuración de funcionamiento (modelo de lenguaje, embeddings, troceado, recuperación,
conversación y límites de peticiones) se guarda en la base de datos y se edita en
*Administración → Configuración*, sin reiniciar. Cada grupo se guarda por separado; los del
modelo de lenguaje y los embeddings tienen **Probar conexión** (prueba los valores del
formulario sin guardarlos), y si un cambio obliga a reindexar se ofrece hacerlo en el momento. Las variables `CHATBOT_*` correspondientes solo se usan en el
**primer arranque**, para rellenarla; después se ignoran (el log avisa si alguna no coincide).
Las claves de API se guardan cifradas con `CHATBOT_SESSION_SECRET`. En `.env` queda lo
necesario para arrancar o que depende del despliegue: datos, sesión, cuenta local de
administración (acceso de rescate), índice y cola. `python -m app.config show` muestra la configuración vigente
y `python -m app.config reset [--group …]` la vuelve a copiar de `.env`. Detalles en la
[guía de despliegue](docs/despliegue.md#configuración-de-la-aplicación).

La administración (http://localhost:5173/admin) se habilita definiendo
`CHATBOT_ADMIN_PASSWORD` (y, recomendado, `CHATBOT_SESSION_SECRET`, de al menos 16 caracteres;
en producción es obligatorio y no se admite la contraseña de ejemplo). Desde ella se edita el
prompt de sistema, que se guarda con histórico de versiones y se aplica a los mensajes nuevos,
se configura el inicio del chat y se gestionan los documentos del RAG (ver
[Formatos de documento](#formatos-de-documento)).

### Usuarios

Por defecto el chat es anónimo, con un historial compartido; la administración puede activar
sesiones privadas y temporales (*Mensaje inicial → Acceso al chat*) para que cada visitante solo
vea sus conversaciones. Con acceso por LDAP (*Administración → Configuración → Usuarios*;
valores iniciales en `CHATBOT_AUTH_MODE` y `CHATBOT_LDAP_*`) cada usuario inicia sesión con su
cuenta del directorio (OpenLDAP o Active Directory) y solo ve sus conversaciones; los miembros
del grupo de administración LDAP administran la aplicación y los del grupo de auditores
(opcional; sin él, los administradores) acceden a la auditoría. Con LDAP, la administración puede
permitir además el acceso anónimo (*Mensaje inicial → Acceso al chat*): cada acceso anónimo
solo ve sus conversaciones durante esa sesión, y se conservan para auditoría. Configuración y
ejemplos en la [guía de despliegue](docs/despliegue.md#usuarios-con-ldap).

### Mensaje inicial y preguntas sugeridas

En *Administración → Mensaje inicial* se puede definir:

- Un **mensaje inicial** (admite Markdown) que aparece como primer mensaje del asistente en cada
  conversación. Es solo de presentación: no se guarda en la conversación ni se envía al modelo.
  Si se deja vacío, el chat muestra la portada habitual.
- Hasta **5 preguntas sugeridas**, que aparecen sobre el campo de texto al empezar una
  conversación. Al pulsar una, su texto sustituye al que hubiera en el campo y se puede editar
  antes de enviarlo.

### Formatos de documento

| Formato | Extensiones | Se cita por |
|---|---|---|
| PDF | `.pdf` | página (el enlace abre el PDF en ella) |
| Markdown | `.md`, `.markdown` | apartado (ruta de encabezados) |
| HTML | `.html`, `.htm` | apartado |
| Word / texto de OpenDocument | `.docx`, `.odt` | apartado (estilos de encabezado) |
| PowerPoint / presentación de OpenDocument | `.pptx`, `.odp` | diapositiva (con sus notas; las ocultas no se indexan) |
| Excel / hoja de cálculo de OpenDocument | `.xlsx`, `.ods` | hoja (las ocultas no se indexan) |

En las hojas de cálculo, la primera fila con datos se toma como cabecera y cada fila se indexa
como «Cabecera: valor; …», para que se entienda sin el resto de la tabla. Los formatos
antiguos (`.doc`, `.xls`, `.ppt`) y los documentos protegidos con contraseña no se admiten:
hay que guardarlos como `.docx`, `.xlsx` o `.pptx` sin contraseña. Los originales de Office y
OpenDocument se descargan al abrir la referencia (el navegador no los muestra).

También se puede subir un **ZIP** con varios documentos: cada fichero admitido se da de alta
por separado (con la categoría y la opción de título elegidas para la subida) y se muestra el
resultado de cada uno. Se omiten los directorios y los ficheros de sistema (`__MACOSX`,
`.DS_Store`, `Thumbs.db`, ocultos); no se admiten ZIP dentro de otro ZIP ni ficheros cifrados.
El tamaño máximo de subida se aplica a cada fichero del ZIP (se comprueba al descomprimirlo, así
que un ZIP malicioso no llena el disco); el ZIP en sí no tiene límite propio, salvo el del proxy
(`CHATBOT_MAX_BODY_SIZE`, ver la [guía de despliegue](docs/despliegue.md)). Los nombres con
acentos de los ZIP creados en Windows se leen bien aunque no estén en UTF-8.

### Título y categorías de los documentos

Cada documento tiene un **título**, que es lo que muestran las referencias del chat y el
selector de documentos. Al subirlo se toma de sus metadatos (el título del PDF, el de la página
HTML o el primer encabezado o el `title` del front matter del Markdown), descartando los que no
son un título real («Documento1», «Microsoft Word - informe.docx»…); si no tiene uno válido, o
se marca **Usar el nombre del fichero como título**, es el nombre del fichero. Los documentos
anteriores a esta versión tienen como título su nombre de fichero. El título se puede cambiar en
*Administración → Documentos → Editar*, y las referencias, también las de respuestas ya dadas,
muestran siempre el vigente.

Las **categorías** (un solo nivel y opcionales) agrupan los documentos: se crean en la misma
página, se eligen al subir (todos los ficheros de la subida van a ella) o al editar el documento,
y la lista se puede filtrar por categoría.

El título, el nombre del fichero (si es distinto) y la categoría se añaden al texto con el que se
calcula el vector de cada fragmento (si está activado *Añadir el título, el fichero, la categoría y la
sección del documento a cada fragmento* en la
configuración de indexación). Por eso, al cambiar el título o la categoría de un documento, o
renombrar o eliminar su categoría, queda **desactualizado**: se marca en la lista y se reindexa
con **Reindexar desactualizados**.

### Embeddings

Los documentos se trocean y se indexan en ChromaDB (`data/chroma`) con el modelo de embeddings
configurado (valores iniciales en `CHATBOT_EMBEDDING_*`):

- `openai` (por defecto): usa el endpoint `/v1/embeddings` del proxy LiteLLM (misma URL que el
  chat salvo que se indique otra). El modelo debe coincidir con el `model_name` configurado en
  LiteLLM.
- `local`: calcula los embeddings en el propio servidor con sentence-transformers
  (`uv sync --extra local-embeddings`; descarga el modelo la primera vez).
- `fake`: simulado, sin red (tests y demos).

Si se cambia de modelo, pulsa **Reindexar todo** en *Administración → Documentos* (vacía el
índice y vuelve a indexar todos los documentos; mientras tanto, el chat responde sin ellos).

Para volúmenes grandes, el índice puede estar en **Qdrant** y la indexación repartirse entre
**workers** con una cola en **Redis** (`docker-compose.escalado.yml`, ver la
[guía de despliegue](docs/despliegue.md#escalado-qdrant-y-workers)).

### Respuestas con referencias

En cada pregunta se recuperan los fragmentos más parecidos (top-k) por encima de la puntuación
mínima, se añaden numerados al prompt de sistema y se pide al modelo que cite
con `[n]`. Bajo la respuesta se muestran las fuentes citadas (fichero y página o sección) con
enlace al original; los PDF se abren en la página citada y en las presentaciones se indica la
diapositiva. Si el modelo no cita, se muestran
como «Fuentes consultadas».

Las preguntas de seguimiento («¿y con cuánta antelación?») se reformulan con el historial antes
de buscar (se puede desactivar en la configuración de recuperación); la respuesta se genera con la pregunta original.

La búsqueda se puede **limitar a uno o varios documentos** (hasta 5) con el botón **«Buscar en»**
que hay sobre el campo de texto del chat, que permite filtrar la lista de documentos por nombre:
la limitación se guarda en la conversación y se aplica a sus preguntas siguientes hasta que se
quita. Bajo cada respuesta limitada se indica en qué documentos se buscó. Entonces solo se
busca en esos documentos (comparando con todos sus fragmentos) y en lugar de la puntuación
mínima general se usa la de la búsqueda limitada (*Recuperación*, 0 por defecto: basta con el
top-k), porque quien pregunta ya ha indicado dónde está la respuesta.

Si la pregunta **nombra un documento**, por su código («¿qué dice el NOR-013?», también «nor
13») o por su título completo («la normativa de control de acceso lógico en DTIC»), la búsqueda
se limita a él solo para esa pregunta y sus preguntas de seguimiento (se reconoce también en la
pregunta reformulada). Si se nombran más de 5 documentos, no se limita. Se puede desactivar en
la configuración de recuperación.

Opcionalmente, a cada fragmento recuperado se le pueden añadir los **fragmentos vecinos** del
mismo documento (hasta 3 antes y después, dentro del máximo de contexto), unidos en un pasaje
sin repetir el solapamiento: útil cuando la respuesta queda partida entre fragmentos.

Si la búsqueda se limita a **un solo documento** y este cabe en el límite configurado, se envía
**completo** al modelo, dividido en partes citables por página o apartado («resúmeme el
NOR-013»). Está desactivado por defecto: el límite debe caber en la ventana de contexto del
modelo de chat.

Antes de indexar se limpia el texto: se quitan de los PDF los encabezados y pies que se repiten
en todas las páginas (sellos de firma electrónica, «Página 3 de 27»…), se quitan los puntos de
relleno de los índices y cada fragmento se busca con el título del documento y su sección
delante. Los fragmentos que parecen el índice del documento pesan menos en la búsqueda. Ver
[limpieza del texto](docs/despliegue.md#limpieza-del-texto-antes-de-indexar).

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
y recomienda el tamaño de fragmento, el solapamiento, el top-k y la puntuación mínima.
Tras cambiar el troceado hay que reindexar los documentos.

La evaluación aplica la misma limpieza del texto y el mismo peso de los fragmentos de índice que
la configuración; con `--sin-preprocesado` se evalúa sin ellos, para comparar el antes y el
después.

Mientras trabaja muestra el avance documento a documento (con una estimación del tiempo que
queda) en la salida de errores, así que `> informe.md` guarda solo el informe; `--sin-progreso`
lo oculta. Con muchos documentos y embeddings locales en CPU, cada tamaño de fragmento puede
tardar bastante, porque se vuelven a calcular los embeddings de todo el corpus.

### Cuadro de mando

*Administración → Cuadro de mando* (la página de entrada de la administración) muestra, para
un rango de fechas: sesiones (solo las que preguntan), usuarios, preguntas, tiempo medio y
mediano de respuesta, porcentaje de preguntas respondidas sin decir que no se tiene la
información, una gráfica diaria con su tabla equivalente y la tabla de preguntas y respuestas
con su hora y tiempo, descargable en CSV y Excel. No muestra quién preguntó. Las preguntas se
guardan en un registro propio que no se pierde al borrar conversaciones. Las frases que
indican «sin información» se configuran en la misma página.

### Auditoría

*Administración → Auditoría* reúne los avisos del proceso de revisión (posible uso malicioso,
respuestas inapropiadas e información sensible, detectados por reglas y por un modelo
revisor), las últimas revisiones y el registro completo de preguntas y respuestas con su
usuario, IP y detalles, filtrable y descargable en CSV y Excel. Cada aviso se marca como
revisado o descartado con una nota. La revisión se programa en *Configuración → Auditoría*
(cada hora o a diario), se lanza a mano desde la pantalla o con `python -m app.audit run`, y
puede avisar por correo. Con LDAP, la auditoría puede reservarse a un grupo de auditores. Más
detalles en la [guía de despliegue](docs/despliegue.md#auditoría).

### Estilos y accesibilidad

La interfaz sigue el *Manual de estilos para aplicaciones internas* (WCAG 2.1 AA). Los colores
y tipos de letra son variables CSS (tokens) definidas en `frontend/src/index.css`, con los
nombres del manual (`--color-marca`, `--color-enlace`, `--color-texto-secundario`…); los
componentes no usan colores literales (lo comprueba `src/styles.test.ts`). Cada página tiene
enlace para saltar al contenido, un único `h1`, título de pestaña propio, pie con la versión y
foco visible; por debajo de 768 px la navegación se pliega tras el botón «Menú». Los E2E pasan
axe-core en las pantallas principales (a 1280 y 320 px) y fallan con incidencias graves o
críticas (`e2e/accessibility.spec.ts`).

En *Administración → Apariencia* se configuran el nombre de la aplicación y de la
organización, el logo (SVG, PNG o WebP), la cabecera (clara o del color de marca), los colores,
los tipos de letra (de una lista de fuentes instaladas en los equipos), el pie (unidad
responsable, declaración de accesibilidad y soporte) y los avisos de acceso y de IA. Los
contrastes se comprueban según WCAG 2.1 AA en directo; uno insuficiente no impide guardar, pero
se avisa. El servidor genera con esos valores la hoja `/api/v1/appearance.css`, que se carga
antes de pintar la página. El logo se guarda en la base de datos (entra en las copias de
seguridad).

## API

Documentación interactiva en http://localhost:8000/docs. Endpoints principales:

| Método | Ruta | Descripción |
|---|---|---|
| GET | `/api/v1/health` | Estado de la API y de la BD |
| GET/POST | `/api/v1/conversations` | Listar / crear conversaciones |
| PATCH/DELETE | `/api/v1/conversations/{id}` | Renombrar o limitar la búsqueda a unos documentos (`document_ids`) / eliminar |
| GET | `/api/v1/conversations/{id}/messages` | Mensajes de una conversación |
| GET | `/api/v1/chat/config` | Mensaje inicial y preguntas sugeridas |
| POST | `/api/v1/chat` | Envía un mensaje (con `document_ids`, solo se busca en esos documentos); respuesta en streaming SSE (`conversation`, `scope`, `token`, `sources`, `done`, `error`) |
| GET/POST/DELETE | `/api/v1/auth/session` | Sesión del usuario del chat (modo LDAP) |
| POST | `/api/v1/auth/anonymous` | Nueva sesión anónima: acceso anónimo con LDAP o sesión privada sin él (si la administración lo permite) |
| GET/POST/DELETE | `/api/v1/admin/session` | Estado / inicio / cierre de sesión de administración |
| GET/PUT | `/api/v1/admin/chat-config` | Mensaje inicial y preguntas sugeridas (admin) |
| GET | `/api/v1/appearance`, `/api/v1/appearance.css`, `/api/v1/appearance/logo` | Apariencia pública: textos, hoja de estilos y logo |
| GET | `/api/v1/admin/dashboard/summary?from&to` | Cuadro de mando: sesiones, usuarios, preguntas, tiempos y serie diaria (admin) |
| GET | `/api/v1/admin/dashboard/questions?from&to&page&size` | Preguntas y respuestas del rango, paginadas (admin) |
| GET | `/api/v1/admin/dashboard/questions.csv`, `.xlsx` | Exportación de las preguntas del rango (admin) |
| GET/PUT | `/api/v1/admin/dashboard/no-info-phrases` | Frases que indican «sin información»; al cambiarlas se reclasifica el registro (admin) |
| GET | `/api/v1/admin/audit/summary` | Avisos nuevos, revisión en curso y última revisión (auditor) |
| GET/POST | `/api/v1/admin/audit/runs` | Últimas revisiones / lanzar una revisión (409 si hay otra en curso) (auditor) |
| GET | `/api/v1/admin/audit/alerts?status&category&severity&page&size` | Avisos de la auditoría (auditor) |
| PATCH | `/api/v1/admin/audit/alerts/{id}` | Marcar un aviso como revisado, descartado o nuevo, con nota (auditor) |
| GET | `/api/v1/admin/audit/log?from&to&user&ip&page&size` | Registro completo de preguntas y respuestas (auditor) |
| GET | `/api/v1/admin/audit/log.csv`, `.xlsx` | Exportación del registro completo (auditor) |
| POST | `/api/v1/admin/audit/test-email` | Envía un correo de prueba con la configuración SMTP (auditor) |
| GET/PUT/DELETE | `/api/v1/admin/appearance` | Apariencia: consultar, guardar (con avisos de contraste) y restablecer (admin) |
| PUT/DELETE | `/api/v1/admin/appearance/logo` | Subir o quitar el logo (admin) |
| GET/PUT | `/api/v1/admin/access` | Acceso anónimo (con LDAP) o sesiones privadas (sin LDAP) (admin) |
| GET | `/api/v1/admin/config` | Configuración de la aplicación por grupos, sin secretos (admin) |
| PUT | `/api/v1/admin/config/{grupo}` | Cambia un grupo (`llm`, `embeddings`, `indexing`, `rag`, `chat`, `limits`, `users`, `audit`); avisa si hay que reindexar (admin) |
| POST | `/api/v1/admin/config/{llm,embeddings}/test` | Prueba la conexión con los valores enviados, sin guardarlos (admin) |
| GET/PUT | `/api/v1/admin/prompt` | Prompt de sistema vigente / guardar nueva versión (admin) |
| GET | `/api/v1/admin/prompt/versions` | Historial de versiones (admin) |
| POST | `/api/v1/admin/prompt/versions/{id}/restore` | Restaurar una versión (admin) |
| GET/POST | `/api/v1/admin/documents` | Listar / subir documentos (multipart, varios ficheros o ZIP; `category_id` y `use_filename` opcionales) (admin) |
| GET | `/api/v1/admin/documents/stats` | Nº de documentos, fragmentos, desactualizados y modelo de embeddings (admin) |
| POST | `/api/v1/admin/documents/reindex` | Reindexar todos (admin) |
| POST | `/api/v1/admin/documents/reindex-outdated` | Reindexar los desactualizados (admin) |
| GET/PATCH/DELETE | `/api/v1/admin/documents/{id}` | Detalle / cambiar título o categoría / eliminar (admin) |
| POST | `/api/v1/admin/documents/{id}/reindex` | Reindexar uno (admin) |
| GET | `/api/v1/admin/documents/{id}/file` | Descargar el original (admin) |
| GET/POST | `/api/v1/admin/categories` | Categorías con su nº de documentos / crear una (admin) |
| PATCH/DELETE | `/api/v1/admin/categories/{id}` | Renombrar / eliminar (sus documentos quedan sin categoría) (admin) |
| GET | `/api/v1/documents` | Documentos indexados (id, título, fichero, tipo y categoría), para limitar la búsqueda del chat |
| GET | `/api/v1/documents/{id}/file` | Ver el original desde una referencia del chat (PDF en línea, Markdown como texto, HTML en sandbox; Office y OpenDocument se descargan) |

## Calidad y tests

| | Backend (`backend/`) | Frontend (`frontend/`) |
|---|---|---|
| Formato | `uv run ruff format .` | `npm run format` |
| Lint | `uv run ruff check .` | `npm run lint` |
| Tipos | `uv run mypy app tests` | `npm run typecheck` |
| Tests unitarios | `uv run pytest` (cobertura ≥ 85 %) | `npm test` (cobertura ≥ 80 %) |
| Tests de interfaz | — | `npm run test:e2e` (Playwright) |
| Tests con PostgreSQL | `CHATBOT_TEST_DATABASE_URL=… uv run pytest --no-cov` (ver abajo) | |
| Despliegue | `scripts/docker-smoke.sh`, `ldap-smoke.sh`, `escalado-smoke.sh`, `https-smoke.sh` y `postgres-smoke.sh` (desde la raíz; requieren Docker) | |

Los tests E2E levantan automáticamente backend (puerto 8001, con modelo simulado) y frontend
(puerto 5174) con una carpeta de datos temporal. La primera vez instala el navegador con
`npx playwright install chromium`. Los tests que cambian la configuración global del chat se
ejecutan en un proyecto aparte, después del resto. Si en un equipo lento fallan por tiempo de
espera al ejecutarse en paralelo, prueba con `npm run test:e2e -- --workers=1`.

Los tests del backend usan un SQLite temporal por test. Con `CHATBOT_TEST_DATABASE_URL` usan
en su lugar esa base de datos PostgreSQL, que **vacían antes de cada test** (usa una exclusiva
para los tests) y omiten los que dependen de SQLite:

```bash
docker run -d --name chatbot-pg-test -p 127.0.0.1:55432:5432 -e POSTGRES_USER=chatbot \
  -e POSTGRES_PASSWORD=chatbot -e POSTGRES_DB=chatbot_test postgres:18-alpine
CHATBOT_TEST_DATABASE_URL=postgresql+asyncpg://chatbot:chatbot@127.0.0.1:55432/chatbot_test \
  uv run pytest --no-cov
```

El CI de GitHub Actions ejecuta todo lo anterior en cada pull request.

El backend registra cada petición con un `X-Request-ID` (`CHATBOT_LOG_FORMAT=json` para logs
estructurados) y limita las peticiones por IP al chat y al inicio de sesión (configurable en la
administración).

> **Windows:** ejecuta Vitest desde una ruta con la letra de unidad en mayúscula
> (`F:\...`). Si el terminal se abre en `f:\...`, Vitest carga los módulos dos veces y
> falla con *"Vitest failed to find the current suite"*.
