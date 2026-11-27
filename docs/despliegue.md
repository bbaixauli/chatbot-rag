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
- El servidor de IA (LiteLLM, vLLM, Ollama…) es externo. Su URL, modelos y claves se configuran
  en la administración (ver [Configuración de la aplicación](#configuración-de-la-aplicación)).

> El backend (API) debe ejecutarse con **una sola réplica**: el límite de peticiones se guarda
> en memoria. Para repartir la indexación entre varios procesos, usa el despliegue escalado
> (ver [Escalado](#escalado-qdrant-y-workers)).

## Escalado: Qdrant y workers

Con muchos documentos, o si la indexación ralentiza la API, el despliegue escalado mueve:

- el **índice vectorial** de ChromaDB (embebido en la API) a un servidor **Qdrant**, y
- la **indexación** a uno o varios **workers** que leen una cola de **Redis** (arq), de modo que
  subir o reindexar documentos no consume recursos de la API.

```
navegador ─► frontend (nginx) ─► backend (API) ──► Redis (cola) ──► worker × N
                                     │  │                                │  │
                                     │  └────────── Qdrant (índice) ◄────┘  │
                                     └──── volumen /data (BD y ficheros) ◄──┘
```

```bash
docker compose -f docker-compose.yml -f docker-compose.escalado.yml up -d --build
# Varios workers:
CHATBOT_WORKERS=3 docker compose -f docker-compose.yml -f docker-compose.escalado.yml up -d
```

El fichero `docker-compose.escalado.yml` añade Redis, Qdrant y el servicio `worker` (misma imagen
que el backend con el extra `escalado`), y configura `CHATBOT_VECTOR_STORE=qdrant` y
`CHATBOT_TASK_RUNNER=arq`. Para no repetir `-f` en cada comando, define en `.env`:

```dotenv
COMPOSE_FILE=docker-compose.yml:docker-compose.escalado.yml
```

(en Windows, separa los ficheros con `;`).

Notas:

- **Paso desde el despliegue normal**: tras arrancar el escalado, pulsa **Reindexar todo** en
  *Administración → Documentos* para llevar el índice a Qdrant.
- Cada worker indexa hasta `CHATBOT_INDEXING_CONCURRENCY` documentos a la vez (2 por defecto).
  Si un worker se detiene a mitad de un documento, el trabajo se reintenta (hasta 3 veces).
- La API y los workers comparten la base de datos. Con SQLite (en modo WAL) solo uno escribe a
  la vez: con varios workers, usa [PostgreSQL](#base-de-datos-postgresql). Para Qdrant o Redis
  externos, cambia `CHATBOT_QDRANT_URL` (y `CHATBOT_QDRANT_API_KEY`, solo con `https://`) y
  `CHATBOT_REDIS_URL`.
- Las copias de seguridad funcionan igual. Al restaurar se vacía también la colección de Qdrant,
  y los workers reindexan los documentos.
- `scripts/escalado-smoke.sh` comprueba este despliegue con dos workers.

## Base de datos PostgreSQL

Por defecto, la base de datos es un fichero SQLite en el volumen de datos, suficiente para una
instalación con pocos usuarios. PostgreSQL es preferible con el despliegue escalado (la API y
varios workers escriben a la vez) o si la organización prefiere una base de datos gestionada.
Los documentos subidos y el índice siguen en el volumen de datos (o en Qdrant).

La aplicación crea y actualiza las tablas al arrancar (migraciones), igual que con SQLite. Solo
admite instalaciones nuevas: no hay migración de los datos de una instalación SQLite.

La contraseña va en `CHATBOT_DATABASE_PASSWORD`, no en la URL: así no hay que codificar sus
caracteres especiales y no aparece cuando se muestra la URL (logs, diagnóstico).

### En un contenedor: docker-compose.postgres.yml

En `.env`:

```dotenv
CHATBOT_DATABASE_URL=postgresql+asyncpg://chatbot@postgres:5432/chatbot
# Genera una con: python -c "import secrets; print(secrets.token_urlsafe(24))"
CHATBOT_DATABASE_PASSWORD=<contraseña>
COMPOSE_FILE=docker-compose.yml:docker-compose.postgres.yml
```

Con el escalado y HTTPS:
`COMPOSE_FILE=docker-compose.yml:docker-compose.escalado.yml:docker-compose.postgres.yml:docker-compose.https.yml`
(en Windows, separados por `;`).

```bash
docker compose up -d --build
docker compose ps        # postgres debe aparecer como "healthy"
```

- Añade el servicio `postgres` (PostgreSQL 18), sin puertos publicados, con los datos en el
  volumen `postgres-data`. La API espera a que esté listo para arrancar.
- **La contraseña se fija al crear el volumen.** Cambiarla después en `.env` no cambia la de
  PostgreSQL: cámbiala también en la base de datos y recrea la API y los workers:

  ```bash
  docker compose exec postgres psql -U chatbot -c "ALTER ROLE chatbot PASSWORD 'nueva'"
  # Actualiza CHATBOT_DATABASE_PASSWORD en .env y:
  docker compose up -d
  ```

- Consola SQL: `docker compose exec postgres psql -U chatbot chatbot`.
- **Versión mayor.** La imagen está fijada en PostgreSQL 18. Las actualizaciones menores (18.x)
  llegan con `docker compose pull postgres && docker compose up -d`. Para pasar a otra versión
  mayor hay que volcar y restaurar los datos:

  ```bash
  docker compose stop backend worker frontend
  docker compose exec -T postgres pg_dump -U chatbot -Fc chatbot > chatbot.dump
  docker compose down                                  # conserva los volúmenes
  docker volume rm chatbot-rag_postgres-data           # ¡solo tras comprobar el volcado!
  # Cambia la versión de la imagen en docker-compose.postgres.yml y:
  docker compose up -d postgres --wait
  docker compose exec -T postgres pg_restore -U chatbot -d chatbot --no-owner < chatbot.dump
  docker compose up -d
  ```

### En un servidor PostgreSQL de la organización

No uses `docker-compose.postgres.yml`. El administrador de la base de datos crea el usuario y la
base de datos (probado con PostgreSQL 18):

```sql
CREATE ROLE chatbot LOGIN PASSWORD '…';
CREATE DATABASE chatbot OWNER chatbot ENCODING 'UTF8' TEMPLATE template0;
```

El usuario debe poder crear y modificar tablas en la base de datos (las migraciones se aplican
al arrancar): basta con que sea su propietario. En `.env`:

```dotenv
CHATBOT_DATABASE_URL=postgresql+asyncpg://chatbot@pg.ejemplo.es:5432/chatbot?ssl=verify-full
CHATBOT_DATABASE_PASSWORD=<contraseña>
# CA que firma el certificado del servidor, en el volumen de datos
PGSSLROOTCERT=/data/postgres-ca.pem
```

```bash
docker compose cp ca.pem backend:/data/postgres-ca.pem
docker compose up -d
```

- **TLS.** Con `?ssl=verify-full` la conexión va cifrada y se comprueba el certificado del
  servidor (CA y nombre) con la CA de `PGSSLROOTCERT`. `?ssl=require` cifra sin comprobarlo
  (no recomendable). La CA no puede indicarse en la propia URL.
- **Conexiones.** La API y cada worker abren hasta 15 conexiones; comprueba que caben en
  `max_connections` del servidor.
- El servidor debe ser accesible desde el contenedor `backend` (y los workers).


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
| `CHATBOT_LLM_BASE_URL`, `CHATBOT_LLM_API_KEY`, `CHATBOT_LLM_MODEL` | Servidor de IA y modelo de chat (valores iniciales, ver abajo) |
| `CHATBOT_EMBEDDING_MODEL` (y `CHATBOT_EMBEDDING_BASE_URL` si es otro servidor) | Modelo de embeddings (valor inicial) |
| `CHATBOT_ADMIN_PASSWORD` | Contraseña de la administración (no se admite la de ejemplo de `.env.example`) |
| `CHATBOT_SESSION_SECRET` | Clave aleatoria de al menos 16 caracteres, obligatoria si hay administración o LDAP: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |
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
| `CHATBOT_MAX_BODY_SIZE` | `60m` | Tamaño máximo de una petición en nginx (debe superar el tamaño máximo de subida de la configuración). Limita también el tamaño de los ZIP con varios documentos: súbelo si se van a subir ZIP grandes (por ejemplo `500m`) |
| `CHATBOT_UV_EXTRAS` | — | `local-embeddings` para incluir sentence-transformers en la imagen |
| `CHATBOT_IMAGE_TAG` | `latest` | Etiqueta de las imágenes construidas |
| `CHATBOT_TRUSTED_PROXIES` | — | IP o rangos del proxy HTTPS propio, para conocer la IP real de los clientes (ver [HTTPS](#https)) |

Con el proveedor de embeddings `local`, el modelo se descarga la primera vez en
`/data/modelos` y se conserva entre reinicios.

## Configuración de la aplicación

Los parámetros de funcionamiento se guardan en la base de datos y se cambian en
*Administración → Configuración*, sin reiniciar (con **Probar conexión** para el modelo de
lenguaje y los embeddings):

| Grupo | Variables de `.env` (valores iniciales) |
|---|---|
| Modelo de lenguaje | `CHATBOT_LLM_*` |
| Embeddings | `CHATBOT_EMBEDDING_*` |
| Indexación | `CHATBOT_UPLOAD_MAX_MB`, `CHATBOT_CHUNK_SIZE_CHARS`, `CHATBOT_CHUNK_OVERLAP_CHARS`, `CHATBOT_INDEXING_*` |
| Recuperación | `CHATBOT_RAG_*` |
| Conversación | `CHATBOT_CHAT_*`, `CHATBOT_SYSTEM_PROMPT_MAX_LENGTH` |
| Límites de peticiones | `CHATBOT_RATE_LIMIT_*` |
| Usuarios | `CHATBOT_AUTH_MODE`, `CHATBOT_LDAP_*` (salvo `CHATBOT_LDAP_CA_CERT_FILE`) |
| Documentos web | `CHATBOT_WEB_TIMEOUT_SECONDS` (30), `CHATBOT_WEB_ALLOWED_DOMAINS` (dominios separados por comas, con sus subdominios; vacío: cualquiera), `CHATBOT_WEB_REFRESH_ENABLED` (`true`), `CHATBOT_WEB_REFRESH_INTERVAL_HOURS` (24), `CHATBOT_WEB_NOT_FOUND_LIMIT` (3) |
| Certificados | `CHATBOT_TLS_STRICT_X509` (`true`); los certificados de confianza solo se configuran en la administración (ver [Certificados de una CA propia](#certificados-de-una-ca-propia)) |

En el **primer arranque** se copian de `.env` a la base de datos; a partir de ahí esas
variables **se ignoran** (si alguna no coincide con lo guardado, el log lo avisa:
«`CHATBOT_LLM_MODEL` se ignora…»). Una instalación existente se migra sola al actualizar. Si
se define `CHATBOT_DEFAULT_SYSTEM_PROMPT`, pasa a ser la primera versión del prompt de sistema.

Siguen en `.env`, porque hacen falta para arrancar o dependen del despliegue: entorno, datos y
base de datos, logs, sesión (`CHATBOT_SESSION_*`), cuenta local de administración, índice y
cola (`CHATBOT_VECTOR_STORE`, `CHATBOT_QDRANT_*`, `CHATBOT_TASK_RUNNER`, `CHATBOT_REDIS_URL`,
`CHATBOT_INDEXING_CONCURRENCY`) y el fichero de la CA del directorio
(`CHATBOT_LDAP_CA_CERT_FILE`).

- Las **claves de API** se guardan cifradas con una clave derivada de
  `CHATBOT_SESSION_SECRET` y nunca se muestran. Si cambias `CHATBOT_SESSION_SECRET` (o
  restauras una copia en un servidor con otra), no se pueden descifrar: se usa el valor de
  `.env` y la administración pide volver a introducirlas.
- Cambiar el **modelo de embeddings**, el **troceado** o la **limpieza del texto** obliga a
  reindexar: al guardar se
  ofrece «Reindexar todo ahora» y *Documentos* avisa mientras queden documentos indexados con la
  configuración anterior. No se permite el cambio mientras haya documentos indexándose.
- Los workers de indexación leen la configuración antes de cada documento.
- Los **documentos web** los descarga el backend desde su red al añadirlos; la
  **actualización periódica** la programa el backend y la hacen los workers (o el propio
  backend, sin ellos). Todos esos contenedores tienen que poder resolver y alcanzar las
  direcciones (proxy de salida, DNS interno). Como pueden llegar a servicios internos, conviene
  limitar los **dominios permitidos** a los de la intranet o las webs de la organización.

#### Certificados de una CA propia

Las conexiones HTTPS con los servidores de los **documentos web**, del **modelo de lenguaje** y
de los **embeddings** solo confían en las CA públicas (las del paquete `certifi`, no las del
sistema): **instalar la CA de la organización en el contenedor no sirve**. Si alguno de esos
servidores usa un certificado de una CA propia, pega su certificado (PEM, uno o varios, con las
líneas `BEGIN`/`END CERTIFICATE`) en *Administración → Configuración → Certificados →
Certificados de confianza*. Se suman a las CA públicas y se aplican sin reiniciar (también en
**Probar conexión**). El directorio LDAP tiene su propio certificado, en *Usuarios*.

La **comprobación X.509 estricta** (activada por defecto) exige que los certificados cumplan el
RFC 5280. Algunas CA corporativas emiten certificados que no la superan, sobre todo porque les
falta el *Authority Key Identifier*. El error lo indica («no supera la comprobación X.509
estricta (Missing Authority Key Identifier)»), y entonces se puede desactivar en el mismo grupo.
Desactivarla **no** desactiva la verificación: se siguen comprobando la CA, la caducidad y el
nombre del servidor. No hay ninguna opción para dejar de verificar los certificados.

Para ver qué identificadores de clave tiene el certificado de un servidor:

```bash
openssl s_client -connect intranet.ejemplo.org:443 </dev/null 2>/dev/null \
  | openssl x509 -noout -text | grep -A1 "Key Identifier"
```

#### Limpieza del texto antes de indexar

Antes de trocear e indexar un documento se limpia el texto, para que la búsqueda no se
confunda con lo que se repite en todas las páginas. Cada paso se puede desactivar en
*Indexación*:

- **Encabezados y pies repetidos** (`CHATBOT_INDEXING_STRIP_REPEATED_LINES`): en los PDF de 3
  o más páginas se quitan las líneas que aparecen en al menos la proporción de páginas
  indicada (`CHATBOT_INDEXING_REPEATED_LINE_MIN_RATIO`, 0,5 por defecto). Para compararlas se
  ignoran mayúsculas, espacios y números, así que se quitan los sellos de firma electrónica
  (con su código de verificación y fecha), las cabeceras corporativas y los «Página 3 de 27».
- **Puntos de relleno de los índices** (`CHATBOT_INDEXING_CLEAN_TOC_LEADERS`):
  «Objetivo ........ 5» pasa a «Objetivo 5».
- **Título del documento y sección** (`CHATBOT_INDEXING_CONTEXT_PREFIX`): el vector de cada
  fragmento se calcula con «Documento: <nombre del fichero>» y la sección delante, así que
  las preguntas que nombran el documento («¿qué dice el NOR-013?») encuentran sus
  fragmentos. Las fuentes que se muestran conservan el texto original.
- **Apartados de los PDF** (`CHATBOT_INDEXING_PDF_HEADINGS`): en un PDF cada página es una
  sección sin título; se detectan las líneas de encabezado numeradas («4.2 Altas de
  usuarios», o el número en una línea y el título en la siguiente) o con palabra clave
  («CAPÍTULO V», «Anexo II», «Artículo 5») y cada fragmento guarda el apartado al que
  pertenece, como en Markdown, HTML o Word. Para no confundirlos con listas numeradas, solo
  cuentan los que siguen la numeración (tras el 4.2 vienen el 4.2.1, el 4.3 o el 5) y no
  terminan en punto; los anexos la reinician y las páginas de índice se ignoran. El apartado
  entra en el vector del fragmento, la fuente lo muestra («NOR-013, pág. 5 — 4.2 Altas de
  usuarios») y se puede preguntar por él (ver [Apartados citados en la
  pregunta](#apartados-citados-en-la-pregunta)). Activarlo o desactivarlo solo deja
  desactualizados los PDF.

Además, los fragmentos que parecen el índice del documento (tabla de contenidos) se marcan al
indexar y la búsqueda multiplica su puntuación por el **peso de los fragmentos de índice**
(*Recuperación*, `CHATBOT_RAG_TOC_SCORE_FACTOR`, 0,8 por defecto; 1 no cambia nada). Así no
desplazan a las páginas de contenido, que es lo que hacía que el modelo respondiera que solo
tenía el índice del documento.

Si hay documentos indexados pero ningún fragmento llega a la puntuación mínima (o falla la
búsqueda), el modelo no recibe documentación ni, por tanto, las instrucciones de citar y de
no inventar datos. Con **«Avisar al modelo si no encuentra documentación»** (*Recuperación*,
`CHATBOT_RAG_NO_SOURCES_NOTE`, activado por defecto) se le añade una nota: no se ha
encontrado documentación interna para la pregunta y, si la respuesta depende de documentos o
normas internas, debe decir que no dispone de esa información en lugar de suponerla. Las
preguntas generales («¿qué hora es en Tokio?») las sigue respondiendo. Sin documentos
indexados no se añade. Desactívalo si el prompt de sistema ya da esa instrucción.

Tras actualizar a una versión que cambia esta limpieza, *Documentos* indica que hay
documentos desactualizados: pulsa *Reindexar todo*.

#### Búsqueda limitada a unos documentos

Cuando la búsqueda del chat se limita a unos documentos, se comparan todos sus fragmentos con
la pregunta (búsqueda exacta) y se usa la **puntuación mínima al buscar en documentos
concretos** (*Recuperación*, `CHATBOT_RAG_SCOPED_MIN_SCORE`, 0 por defecto) en lugar de la
general: basta con el top-k. El registro de preguntas guarda a qué documentos se limitó cada
búsqueda y si fue porque se eligieron en el chat o porque la pregunta los nombraba.

En el chat también se pueden elegir **categorías** enteras, sin el límite de 5 documentos:
se busca en sus documentos indexados de cada momento (un documento que se añada a la categoría
entra en las preguntas siguientes) y en los documentos sueltos elegidos. Una categoría que se
borra se quita de la limitación; si no queda ningún documento, se busca en todos. Cuando la
búsqueda abarca más de 5 documentos se usa la **puntuación mínima general**, como en una
búsqueda sin limitar, y la localización de apartados no se aplica. Con más de 50 documentos,
en lugar de comparar la pregunta con todos sus fragmentos (unos 5 ms por documento) se usa el
índice con un filtro. La auditoría muestra dónde se buscó cada pregunta (categorías o
documentos) y la exportación lo incluye en la columna «Búsqueda limitada a».

Con **«Buscar solo en los documentos que nombre la pregunta»** (`CHATBOT_RAG_DETECT_DOCUMENT_NAMES`,
activado por defecto), la búsqueda se limita a los documentos que la pregunta nombra:

- por su **código**: letras mayúsculas y números del nombre del fichero («NOR-013»,
  «PRO_02», «IT0045»), que en la pregunta se reconocen con o sin guion, en minúsculas y sin
  ceros a la izquierda («nor 13»);
- por su **título completo**, sin tener en cuenta mayúsculas ni acentos, con o sin la versión
  y el código («Control de acceso lógico en DTIC»). Los títulos de menos de 3 palabras o 15
  caracteres no se reconocen, para no confundirlos con palabras sueltas.

Si se nombran más de 5 documentos no se limita. Los documentos elegidos en el chat tienen
prioridad. `app.diagnose query` aplica la misma detección e indica qué documentos ha
reconocido.

#### Fragmentos vecinos

Si la respuesta queda partida entre dos fragmentos (una tabla que sigue en la página siguiente,
un procedimiento que empieza al final de un fragmento), con **«Fragmentos vecinos que se
añaden»** (*Recuperación*, `CHATBOT_RAG_NEIGHBOR_CHUNKS`, de 0 a 3; 0 por defecto) se añaden a
cada fragmento recuperado los anteriores y posteriores del mismo documento: primero los más
cercanos y los de los fragmentos más relevantes, mientras quepan en el máximo de contexto. Los
fragmentos contiguos se unen en un solo pasaje sin repetir el solapamiento del troceado, y la
fuente indica el rango de páginas («págs. 4-5»). Aumenta el texto que se envía al modelo en
cada pregunta: conviene subir también el máximo de contexto.

#### Documento completo

Preguntas como «resúmeme el NOR-013» no se pueden responder con unos fragmentos: piden el
documento entero. Con **«Documento completo hasta (caracteres)»** (*Recuperación*,
`CHATBOT_RAG_FULL_DOCUMENT_MAX_CHARS`, 0 por defecto: desactivado), si la búsqueda se limita a
**un solo documento** (elegido en el chat o nombrado en la pregunta) y su texto cabe en ese
límite, se envía completo al modelo en lugar de fragmentos. Se reconstruye a partir de los
fragmentos indexados (ya sin encabezados repetidos y sin repetir el solapamiento), dividido en
partes citables: una por página en los PDF y una por apartado en Markdown y HTML. Si el modelo
no cita ninguna parte, el documento aparece una sola vez como fuente. Si no cabe, se usan los
fragmentos más parecidos de ese documento, como siempre.

Este límite sustituye al máximo de contexto en ese caso, así que debe caber en la **ventana de
contexto del modelo de chat** (en español, unos 3,5 caracteres por token), junto con el
prompt de sistema y el historial: por ejemplo, 60000 caracteres son unos 17000 tokens. Cada
pregunta sobre un documento completo es más lenta y consume más tokens. `app.diagnose query`
indica si se enviaría el documento completo y su tamaño. Con la ventana de contexto
configurada (ver abajo), si no cabe se envían sus fragmentos.

#### Apartados citados en la pregunta

«¿Qué dice el apartado 4.2 del NOR-013?» depende de la búsqueda por similitud, y el número del
apartado apenas influye en ella: el fragmento que lo contiene puede quedarse fuera. Con
**«Añadir el apartado que cite la pregunta»** (*Recuperación*, `CHATBOT_RAG_LOCATE_SECTIONS`,
activado por defecto), cuando la búsqueda está limitada a unos documentos (elegidos en el chat
o nombrados en la pregunta) y no se envían completos, se buscan sus encabezados y se añade
**el apartado citado completo**, hasta el siguiente del mismo nivel o superior, aunque ocupe
varias páginas. Va delante de los fragmentos más parecidos, dentro del máximo de contexto y
de la ventana del modelo; si no cabe entero, se envía su principio.

Se reconocen, en la pregunta o en su reformulación y sin tener en cuenta mayúsculas ni
acentos:

- **Por número**, siempre con una palabra delante: «apartado 4.2», «punto 3», «sección
  2.1.3», «capítulo 5» o «capítulo V», «anexo II», «artículo 12», «epígrafe 4», «cláusula 7».
  Un número suelto («4.2 millones») no cuenta.
- **Por título**: entre comillas («Bajas de usuarios») o en la forma «el apartado / el punto /
  la sección de altas de usuarios». Solo cuenta si coincide con un encabezado del documento.

Los encabezados son los apartados de Markdown, HTML, Word y OpenDocument, y los de los PDF
detectados al indexar (ver [Limpieza del texto](#limpieza-del-texto-antes-de-indexar)); en los
PDF indexados antes, se buscan en el texto de sus páginas. Los fragmentos de índice no cuentan.
En Word, la numeración automática no forma parte del texto del encabezado: ahí solo funcionan
las referencias por título. El log indica los apartados citados y los añadidos, y
`app.diagnose query` los muestra; `app.diagnose index` lista los apartados de cada documento.

#### Ventana de contexto del modelo

El prompt que recibe el modelo de chat (prompt de sistema con la documentación, historial y
pregunta) tiene que caber en su **ventana de contexto**, junto con la respuesta. Algunos
servidores **recortan sin avisar** el prompt que no cabe, y lo hacen por el principio, que es
donde va la documentación: el modelo responde que no tiene la información aunque se le haya
enviado (pasa con Ollama y su `num_ctx` por defecto, de 2048 o 4096 tokens).

En *Configuración → Modelo de lenguaje*, indica en **«Ventana de contexto (tokens)»**
(`CHATBOT_LLM_CONTEXT_WINDOW_TOKENS`, 0 por defecto: sin comprobar) la ventana que tiene el
modelo **en el servidor**:

| Servidor | Dónde se configura |
|---|---|
| Ollama | `num_ctx` (en el `Modelfile`, la variable `OLLAMA_CONTEXT_LENGTH` o las opciones del modelo) |
| vLLM | `--max-model-len` |
| LiteLLM | `max_input_tokens` en `model_info` (lo que admita el servidor de detrás) |

De ella se reserva **«Reserva para la respuesta»** (`CHATBOT_LLM_RESPONSE_RESERVE_TOKENS`,
1024). El tamaño del prompt se estima (unos 3,5 caracteres por token, sin el tokenizador del
modelo) y, si no cabe, se ajusta **antes** de enviarlo, en este orden:

1. Si iba el **documento completo**, se envían sus fragmentos más parecidos.
2. Se quita el **historial antiguo**, conservando el último intercambio.
3. Se quitan los **fragmentos** de menor puntuación.
4. Se quita el resto del historial.
5. Si ni el prompt de sistema y la pregunta caben, no se envía: el chat responde que la
   pregunta es demasiado larga para el modelo configurado.

Cada ajuste queda en el log («Prompt ajustado a la ventana del modelo…») y en el registro de
preguntas: la **auditoría** muestra el tamaño del prompt y lo que se quitó, y el **cuadro de
mando** cuenta las preguntas con el prompt recortado. Al guardar, la administración avisa si
«Documento completo hasta» no cabe en la ventana.

Además, se pide al servidor que devuelva cuántos tokens ha contado (`usage`), y se compara con
lo estimado:

- Si cuenta **bastantes menos** de los estimados, lo más probable es que haya recortado el
  prompt porque la ventana configurada es mayor que la del servidor. Esta comprobación
  funciona aunque la ventana no esté configurada.
- Con la ventana configurada, si cuenta **casi toda la ventana** (el 98 % o más), lo más
  probable es que la estimación se quedara corta y el servidor recortara el prompt al tope.
- Con la ventana configurada, si cuenta **más que el límite** (la ventana menos la reserva
  para la respuesta), la estimación se quedó corta: el prompt ocupó parte de la reserva o no
  cabía.

Los dos primeros casos quedan en el log («probablemente lo ha recortado») y en la auditoría
como «recortado por el servidor». El tercero queda en el log («la estimación se ha quedado
corta») y en la auditoría como «estimación por debajo del real». Las dos marcas cuentan en el
cuadro de mando entre las preguntas con el prompt recortado.

`app.diagnose query` muestra el tamaño estimado del prompt, por partes, frente al límite.

Para saber si la estimación es fiable con tu modelo, `app.diagnose tokens` compara lo estimado
con lo que contó el servidor en las preguntas de los últimos 30 días (`--days N` para otro
periodo), por modelo: la mediana y el percentil 95 de la relación real / estimado, cuántas
preguntas superaron lo estimado y el límite actual, y cuántos caracteres por token cubrirían el
95 % de los prompts. Si el percentil 95 pasa de 1, la estimación se queda corta en más del 5 %
de los prompts y el servidor podría recortarlos: deja más reserva para la respuesta o una
ventana algo menor de la real. Termina con código 2 en ese caso.

Para consultarla o, si una configuración deja la aplicación inservible, volver a los valores de
`.env`:

```bash
docker compose exec backend python -m app.config show
docker compose exec backend python -m app.config reset --group llm   # sin --group: todo
docker compose restart backend
```

## Usuarios con LDAP

El modo de acceso y la conexión con el directorio se configuran en *Administración →
Configuración → Usuarios* (los valores iniciales se toman de `CHATBOT_AUTH_MODE` y
`CHATBOT_LDAP_*`, ver [Configuración de la aplicación](#configuración-de-la-aplicación)).

Por defecto (acceso «Sin inicio de sesión») el chat es anónimo: todas las conversaciones forman
un único historial compartido y la administración usa la cuenta local
(`CHATBOT_ADMIN_USERNAME`/`CHATBOT_ADMIN_PASSWORD`, siempre en `.env`).

**Sesiones privadas (sin LDAP)**: en *Administración → Mensaje inicial → Acceso al chat* se
puede marcar «Sesiones privadas y temporales» (desactivado por defecto). Cada visitante recibe
al entrar su propia sesión (`anon:<uuid>`), sin pantalla de inicio de sesión: solo ve sus
conversaciones y, al pulsar «Terminar sesión» (o al caducar la sesión,
`CHATBOT_SESSION_MAX_AGE_SECONDS`), empieza una nueva sin conversaciones. Mientras esté
activado, el historial compartido no se muestra (no se borra) y vuelve a verse al desactivarlo;
las conversaciones privadas no aparecen en él. Todas se conservan en la base de datos para
auditoría y cada sesión queda en el log («Sesión privada», con su `user_id`). Crear sesiones
privadas tiene su propio límite por IP (el del chat), no el de
los inicios de sesión, para no bloquear a varias personas detrás de una misma IP. La sesión de
administración y la sesión privada son independientes: entrar o salir de una no cierra la otra.

Con acceso por **usuarios del directorio (LDAP)**:

- Cada usuario inicia sesión con su cuenta del directorio y **solo ve sus conversaciones**.
- Los miembros del grupo de administración acceden a la administración con su usuario. Las versiones del prompt registran quién las guardó.
- La cuenta local de administración sigue funcionando si está definida: sirve como acceso de
  emergencia cuando el directorio no está disponible. Sus credenciales nunca se envían al
  directorio. Si no se necesita, deja `CHATBOT_ADMIN_PASSWORD` vacío.

El backend busca al usuario con la cuenta de servicio, comprueba la contraseña haciendo *bind*
con su DN y, después, comprueba la pertenencia al grupo de administración. Los valores
introducidos se escapan antes de construir los filtros LDAP.

Salvaguardas al guardar la configuración de usuarios:

- Para activar LDAP o cambiar su conexión, el directorio tiene que responder con esos valores
  (cuenta de servicio y base de usuarios); si no, no se guarda. **Probar conexión** hace la
  misma comprobación sin guardar y, si se indica un usuario de prueba, comprueba también su
  inicio de sesión y si es administrador.
- No se guarda una configuración con la que nadie podría entrar en la administración (sin
  cuenta local y sin grupo de administración LDAP).
- Al cambiar de modo, las sesiones abiertas del modo anterior se cierran.

Valores iniciales de ejemplo en `.env` (también se pueden introducir directamente en la
administración):

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
# Opcional: quién accede a la auditoría (sin él, los administradores)
CHATBOT_LDAP_AUDITOR_GROUP_DN=cn=chatbot-auditores,ou=groups,dc=ejemplo,dc=org
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
CHATBOT_LDAP_AUDITOR_GROUP_DN=CN=Chatbot-Auditores,OU=Grupos,DC=empresa,DC=local
CHATBOT_LDAP_NESTED_GROUPS=true
```

Notas:

- **TLS**: usa `ldaps://` o StartTLS; el certificado del servidor se verifica siempre. Si la
  CA es interna, pega su certificado (PEM) en *Certificado de la CA*, o copia el fichero al
  volumen (por ejemplo `docker compose cp ca.pem backend:/data/ldap-ca.pem`) y define en `.env`
  `CHATBOT_LDAP_CA_CERT_FILE=/data/ldap-ca.pem`. Ese fichero es del despliegue: sigue en `.env`
  y se lee en cada conexión (se puede renovar sin tocar la administración); el certificado
  pegado en la administración tiene prioridad.
- La contraseña de la cuenta de servicio se guarda cifrada, como las claves de API.
- El identificador del usuario es el valor del atributo del identificador en minúsculas.
  Si se cambia ese atributo, los usuarios dejarán de ver sus conversaciones anteriores.
- Los permisos se leen al iniciar sesión: si se añade o quita a alguien del grupo de
  administración, el cambio se aplica en su siguiente inicio de sesión (la sesión dura
  `CHATBOT_SESSION_MAX_AGE_SECONDS`, 8 horas por defecto).
- Las conversaciones creadas en modo anónimo pertenecen al usuario `anonymous` y no aparecen
  en el historial de nadie al activar LDAP.
- **Acceso anónimo opcional**: en *Administración → Mensaje inicial → Acceso al chat* se puede
  marcar «Permitir el acceso anónimo» (desactivado por defecto). La pantalla de inicio de
  sesión muestra entonces «Acceder de forma anónima». Cada acceso anónimo recibe un id propio
  (`anon:<uuid>`): solo ve sus conversaciones, no las de otras personas anónimas ni las de los
  usuarios del directorio, y al cerrar sesión (o caducar la sesión) deja de verlas; un nuevo
  acceso empieza sin conversaciones. Las conversaciones se **conservan** en la base de datos
  para auditoría (`user_id` empieza por `anon:`) y cada acceso queda en el log («Acceso
  anónimo», con su `user_id`). Un acceso anónimo nunca administra. Al desmarcar la casilla, las
  sesiones anónimas abiertas dejan de funcionar en su siguiente petición. Ten en cuenta que,
  activado, cualquiera que llegue a la aplicación puede usar el chat y abrir los documentos
  citados en las respuestas.
- Si el directorio no responde, el inicio de sesión muestra un aviso y el log del backend
  registra el motivo. Las sesiones ya iniciadas siguen funcionando.
- Es imprescindible definir `CHATBOT_SESSION_SECRET`; con HTTPS, también
  `CHATBOT_SESSION_COOKIE_SECURE=true`.

## Apariencia e identidad corporativa

Los colores, tipos de letra, logo y textos del pie se configuran en *Administración →
Apariencia*. Los valores para el Ayuntamiento de Valladolid, según su manual de estilos para
aplicaciones internas, están en [estilos-valladolid.md](estilos-valladolid.md).

## HTTPS

El contenedor `frontend` sirve HTTP. Para servir la aplicación por HTTPS hay dos opciones:

- **`docker-compose.https.yml`** (recomendada si no hay otro proxy): añade
  [Caddy](https://caddyserver.com) delante de la aplicación, con un certificado autofirmado o
  con el de la organización. Sirve igual en una DMZ que en la red interna, y no necesita salida
  a Internet: los certificados nunca se piden a Let's Encrypt ni a otra CA pública.
- **El proxy inverso de la organización** (nginx, Traefik, un balanceador…), ver
  [Con un proxy HTTPS propio](#con-un-proxy-https-propio).

### Con docker-compose.https.yml

```
navegador ──► caddy (:443, :80 redirige) ──► frontend (nginx) ──► backend
               └ volumen caddy-data: CA interna y certificados
```

En `.env`:

```bash
# Nombre (o IP) con el que acceden los usuarios; varios separados por comas
CHATBOT_DOMAIN=chatbot.ejemplo.es
# interno (autofirmado) | certificado (el de la organización)
CHATBOT_TLS=certificado
# Para no repetir -f en cada comando de docker compose (logs, copias…)
COMPOSE_FILE=docker-compose.yml:docker-compose.https.yml
```

Y arranca como siempre:

```bash
docker compose up -d --build
docker compose ps        # caddy, frontend y backend deben aparecer como "healthy"
```

Con el escalado, añade el fichero:
`COMPOSE_FILE=docker-compose.yml:docker-compose.escalado.yml:docker-compose.https.yml`.

Este compose:

- Publica el puerto 443 (TCP, y UDP para HTTP/3) y el 80, que solo redirige a HTTPS. El
  frontend deja de publicar el 8080.
- Activa la cookie de sesión segura (`CHATBOT_SESSION_COOKIE_SECURE=true`) y la cabecera HSTS.
- Mantiene el streaming del chat sin búfer, sin límite propio de tamaño de subida (el de nginx,
  `CHATBOT_MAX_BODY_SIZE`, sigue aplicándose).
- Conserva la IP real de los clientes para el límite de peticiones y la auditoría: Caddy
  sustituye el `X-Forwarded-For` que envíe el cliente por su IP, y nginx se fía solo de Caddy
  (ver `CHATBOT_TRUSTED_PROXIES` abajo).

| Variable | Por defecto | Uso |
|---|---|---|
| `CHATBOT_DOMAIN` | — (obligatoria) | Nombres o IP del servidor. Deben coincidir con los del certificado |
| `CHATBOT_TLS` | `interno` | `interno` (autofirmado) o `certificado` (el de la organización) |
| `CHATBOT_CERTS_DIR` | `./certs` | Directorio con `cert.pem` y `key.pem` (modo `certificado`) |
| `CHATBOT_HTTPS_PORT` | `443` | Puerto HTTPS |
| `CHATBOT_HTTP_REDIRECT_PORT` | `80` | Puerto HTTP (solo redirige a HTTPS) |
| `CHATBOT_HTTP_BIND` | `0.0.0.0` | Interfaz en la que se publican los puertos |
| `CHATBOT_HSTS_MAX_AGE` | `31536000` | Segundos durante los que el navegador solo usará HTTPS |

**Red interna o DMZ.** Caddy no necesita salida a Internet (el backend sí necesita llegar al
servidor de IA, como siempre). En el cortafuegos basta con abrir el puerto 443 hacia el
servidor; el 80 es opcional (sin él, quien escriba `http://` no será redirigido).

- **En una DMZ** o con usuarios en equipos no gestionados, usa un certificado real (modo
  `certificado`) emitido por una CA en la que confíen los navegadores: comercial, o la
  corporativa si solo acceden equipos de la organización.
- **En la red interna**, lo mejor también es el certificado de la CA corporativa. El modo
  `interno` sirve para pruebas o si no se dispone de él, instalando su CA en los equipos.

#### Certificado autofirmado (`CHATBOT_TLS=interno`)

Caddy crea su propia CA y emite y renueva solo el certificado del servidor (también para una
IP). Los navegadores mostrarán un aviso hasta que se instale en los equipos el certificado
raíz de esa CA:

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./chatbot-ca.crt
```

- **Windows**: instálalo en *Entidades de certificación raíz de confianza* del equipo, o
  distribúyelo por directiva de grupo (GPO). Chrome y Edge usan ese almacén; Firefox también,
  si está activada `security.enterprise_roots.enabled` (por defecto en Windows).
- **Linux**: cópialo a `/usr/local/share/ca-certificates/` y ejecuta `update-ca-certificates`.

Mientras los equipos no confíen en la CA, define `CHATBOT_HSTS_MAX_AGE=0`: con HSTS, el
navegador no permite continuar tras el aviso del certificado.

La CA se guarda en el volumen `caddy-data` (es válida unos 10 años). Si se pierde el volumen se
crea otra y hay que volver a instalarla en los equipos: incluye el volumen en las copias (ver
[Copias de seguridad](#copias-de-seguridad)).

#### Certificado de la organización (`CHATBOT_TLS=certificado`)

Copia en `certs/` (o en `CHATBOT_CERTS_DIR`), en formato PEM:

- `cert.pem`: el certificado del servidor seguido de los intermedios (cadena completa; el del
  servidor, primero). Debe incluir en *Nombres alternativos del sujeto* (SAN) los de
  `CHATBOT_DOMAIN`.
- `key.pem`: su clave privada, **sin contraseña**.

Si la CA lo entrega en formato `.pfx` o `.p12` (habitual en una CA de Windows):

```bash
openssl pkcs12 -in certificado.pfx -clcerts -nokeys -out servidor.pem
openssl pkcs12 -in certificado.pfx -cacerts -nokeys -out intermedios.pem
openssl pkcs12 -in certificado.pfx -nocerts -nodes -out certs/key.pem
cat servidor.pem intermedios.pem > certs/cert.pem
chmod 600 certs/key.pem
```

(Quita de `intermedios.pem` el certificado raíz, si viene: no hace falta enviarlo.) El
directorio `certs/` está en `.gitignore`.

Comprueba el certificado que se sirve:

```bash
openssl s_client -connect chatbot.ejemplo.es:443 -servername chatbot.ejemplo.es </dev/null \
  | openssl x509 -noout -subject -issuer -dates
```

**Renovación.** Caddy no vigila los ficheros: tras sustituirlos, recárgalo (sin cortar las
conexiones):

```bash
docker compose exec caddy caddy reload --config /etc/caddy/Caddyfile --force
```

Programa un aviso antes de la fecha de caducidad (`notAfter` en la salida anterior).

### Con un proxy HTTPS propio

Coloca el proxy delante del frontend, apuntando a `http://127.0.0.1:8080`, con
`CHATBOT_HTTP_BIND=127.0.0.1` y `CHATBOT_SESSION_COOKIE_SECURE=true`.

El proxy debe:

- No almacenar en búfer las respuestas de `/api/v1/chat` (streaming SSE). En nginx:
  `proxy_buffering off;` y `proxy_read_timeout 300s;`.
- Permitir subidas del tamaño configurado (`client_max_body_size` en nginx).
- Enviar `X-Forwarded-For` con la IP del cliente (sustituyendo, no añadiendo, la que envíe el
  propio cliente si este no es de confianza) y `X-Forwarded-Proto`.

**IP real de los clientes.** El límite de peticiones y la auditoría usan la IP del cliente.
Por seguridad, el nginx del contenedor ignora las cabeceras `X-Forwarded-*` que reciba: detrás
de otro proxy, todas las peticiones parecerían venir de él. Indica en `.env` la IP (o rangos
CIDR, separados por espacios o comas) del proxy y vuelve a crear el contenedor:

```bash
CHATBOT_TRUSTED_PROXIES=10.0.0.5
```

```bash
docker compose up -d frontend
docker compose logs frontend | grep proxies     # «IP real tomada de X-Forwarded-For de: …»
```

## Actualización

```bash
git pull
docker compose up -d --build
```

Las migraciones de la base de datos se aplican solas al arrancar el backend. Haz antes una copia
de seguridad (ver abajo). Si una versión cambia el modelo de embeddings o el troceado, pulsa
**Reindexar todo** en *Administración → Documentos*: vacía el índice y vuelve a indexar todos
los documentos (mientras tanto, el chat responde sin ellos).

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
- Si el servicio de embeddings o el índice vectorial no responden, por defecto el chat
  responde sin documentación y lo avisa encima de la respuesta (el modelo responde con lo que
  sabe por su cuenta). Con **«Si no se puede consultar la documentación»** (*Recuperación*,
  `CHATBOT_RAG_ON_RETRIEVAL_FAILURE`) en **«No responder y mostrar un error»** (`refuse`), no
  se llama al modelo: el chat muestra «No se puede consultar la documentación en este momento»
  y la pregunta queda en el registro como error. Es lo recomendable si una respuesta sin
  respaldo documental es peor que ninguna.
- Límite por IP y minuto, configurable en la administración (`0` lo desactiva): chat (20) e
  inicios de sesión (5). Al superarlo, la API responde `429` con la cabecera `Retry-After`.

## Auditoría

Cada pregunta queda registrada con su respuesta, el usuario, la IP, el navegador, la hora, el
tiempo de respuesta, el modelo, la versión del prompt, la consulta de búsqueda y los documentos
mostrados. Un proceso revisa periódicamente lo nuevo desde la última ejecución:

- **Reglas** (siempre): datos sensibles en la respuesta (DNI/NIE, IBAN, tarjetas, correos,
  teléfonos, claves) e intentos de manipular al asistente en la pregunta.
- **Modelo revisor** (activable): clasifica intención maliciosa, respuesta inapropiada e
  información sensible. Solo revisa lo de los últimos `CHATBOT_AUDIT_LLM_HISTORY_DAYS` días.
- **Preguntas sin documentación**: si entre las preguntas revisadas hay al menos
  `CHATBOT_AUDIT_RETRIEVAL_FAILURE_THRESHOLD` (10 por defecto; *Configuración → Auditoría*,
  «Alerta por preguntas sin documentación»; 0 la desactiva) en las que no se pudo consultar la
  documentación, se crea **una** alerta de gravedad media para toda la revisión, asociada a la
  última pregunta afectada. No es un problema de esas preguntas, sino del servicio de
  embeddings o del índice: revisa `/api/v1/health` y el log.

Los avisos se gestionan en *Administración → Auditoría*. La revisión se programa allí (cada hora
o a diario) y la ejecuta la propia API; también se puede lanzar a mano desde esa pantalla o
desde un cron externo:

```bash
docker compose exec backend python -m app.audit run
```

**Quién accede.** Sin grupo de auditores (`CHATBOT_LDAP_AUDITOR_GROUP_DN`, también en
*Configuración → Usuarios*), los administradores son también auditores. Con él, solo sus
miembros ven la auditoría: un auditor que no administra entra en la administración y solo ve
esa pantalla, y un administrador que no es auditor no la ve. La cuenta local de administración
(`CHATBOT_ADMIN_PASSWORD`) solo audita si no hay grupo de auditores. Cada consulta a la
auditoría queda en el log («Acceso a la auditoría»).

Nunca hay dos revisiones a la vez. Si se configuran los avisos por correo (SMTP), se envía un
resumen de los avisos de la gravedad elegida, sin los textos de las preguntas. Pasado
`CHATBOT_AUDIT_RETENTION_DAYS` días, el usuario se sustituye por un seudónimo irreversible
(derivado de `CHATBOT_SESSION_SECRET`) y se borran la IP, el navegador y el nombre; los textos
se conservan.

## Copias de seguridad

Una copia incluye la base de datos (conversaciones, prompts, configuración y documentos) y los
ficheros originales subidos. Las claves de API van cifradas con `CHATBOT_SESSION_SECRET`: para
restaurarlas en otro servidor, usa la misma clave o vuelve a introducirlas. El índice vectorial
no se incluye: se reconstruye a partir de los documentos al restaurar.

Los comandos son los mismos con SQLite y con [PostgreSQL](#base-de-datos-postgresql):

- **SQLite**: la copia contiene el fichero de la base de datos.
- **PostgreSQL**: la copia contiene un volcado portable de las tablas (JSON), hecho en una
  única transacción, y la versión del esquema. Se restaura en una instalación con PostgreSQL de
  la misma versión de la aplicación o de una posterior (al restaurar se aplican las migraciones
  que falten).

Una copia de SQLite no se restaura en PostgreSQL ni al revés.

### Crear una copia

Se puede hacer con la aplicación en marcha:

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

Con un servidor PostgreSQL de la organización, su administrador puede hacer además sus propias
copias con `pg_dump`; la de la aplicación sigue haciendo falta por los documentos subidos.

**Con `docker-compose.https.yml` y certificado autofirmado**, guarda también el volumen
`caddy-data`, que contiene la CA interna instalada en los equipos (y su clave privada: protege
la copia):

```bash
docker run --rm -v chatbot-rag_caddy-data:/data:ro -v "$PWD/backups:/copia" alpine \
  tar czf /copia/caddy-data.tar.gz -C /data .
```

Para restaurarlo, con Caddy parado (`docker compose stop caddy`), extrae la copia en el volumen
(`tar xzf /copia/caddy-data.tar.gz -C /data` en el mismo contenedor, sin `:ro`).

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

Con el despliegue escalado, para también los workers (`docker compose stop backend worker`) y
arráncalos después. Con PostgreSQL, la base de datos debe estar en marcha (el comando la
arranca si es el contenedor de `docker-compose.postgres.yml`). La restauración vacía las tablas
de la aplicación (no toca otras del mismo esquema), carga las de la copia y aplica las
migraciones; si se interrumpe, vuelve a ejecutarla con `--force`.

### Instalación nueva a partir de una copia

```bash
docker compose up -d --no-start      # crea el volumen y las imágenes
docker compose run --rm -v "$PWD/backups:/restaurar:ro" --entrypoint python backend \
  -m app.backup restore /restaurar/chatbot-backup-20261118-030000.tar.gz
docker compose up -d
```

Con PostgreSQL, la base de datos debe estar vacía (sin las tablas de la aplicación); si no, usa
`--force`.

## Comprobación del despliegue

`scripts/docker-smoke.sh` construye las imágenes, arranca un entorno aislado con modelos
simulados (puerto 18080) y comprueba el recorrido principal: aplicación web, API, chat en
streaming, subida e indexación de un documento, límite de peticiones, copia de seguridad,
persistencia de los datos y formato de los logs. Al terminar elimina contenedores y volúmenes.
Se ejecuta también en el CI.

`scripts/ldap-smoke.sh` comprueba el modo LDAP contra un OpenLDAP real en Docker, con TLS y una
CA de prueba: rechazo de certificados no verificados, inicio de sesión, rol de administración por
grupo, aislamiento de las conversaciones entre usuarios, intentos de inyección y comportamiento
cuando el directorio no responde. También se ejecuta en el CI.

`scripts/escalado-smoke.sh` comprueba el despliegue escalado con dos workers: indexación desde
la cola de Redis, búsqueda en Qdrant, eliminación de documentos y copia y restauración (con la
colección de Qdrant vaciada y reindexada por los workers). También se ejecuta en el CI.

`scripts/postgres-smoke.sh` comprueba el despliegue con PostgreSQL y el escalado (dos workers
indexando a la vez): migraciones en PostgreSQL (sin SQLite), conversaciones y documentos
guardados en él, estado «degraded» con PostgreSQL parado, recuperación tras reiniciarlo sin
reiniciar la API, persistencia de los datos y que la contraseña (con caracteres especiales) no
aparece en los logs, y la copia de seguridad en formato portable y su restauración (con la
reindexación en Qdrant por los workers). También se ejecuta en el CI.

`scripts/https-smoke.sh` comprueba el despliegue con `docker-compose.https.yml` (puertos 18443
y 18081) en sus dos modos: certificado autofirmado (extrayendo la CA interna) y certificado
propio (con una CA de prueba, incluida su renovación), la redirección a HTTPS, HSTS, la cookie
segura, el streaming del chat sin búfer, una subida grande y la IP real del cliente (sin
aceptar una falsificada). También se ejecuta en el CI.

## Solución de problemas

| Síntoma | Causa probable |
|---|---|
| `backend` no llega a *healthy* | Revisa `docker compose logs backend`: variables mal escritas, migración fallida, falta `CHATBOT_SESSION_SECRET` (o es corta) o `CHATBOT_ADMIN_PASSWORD` es la de ejemplo |
| La respuesta del chat aparece de golpe al final | Un proxy intermedio almacena en búfer el SSE (`proxy_buffering off`) |
| `413 Request Entity Too Large` al subir | Aumenta `CHATBOT_MAX_BODY_SIZE` (y `client_max_body_size` en el proxy externo) |
| El modelo dice que no tiene la información aunque el documento se envía | El servidor recorta el prompt: configura la [ventana de contexto](#ventana-de-contexto-del-modelo) y revisa el log («probablemente lo ha recortado») |
| «No se pudo conectar con el modelo» | El contenedor no alcanza `CHATBOT_LLM_BASE_URL`. Si el servidor de IA está en el mismo equipo, usa su IP o `host.docker.internal`, no `localhost` |
| «El certificado del servidor no es de una CA de confianza» o «no supera la comprobación X.509 estricta» | El servidor (web, modelo o embeddings) usa una CA propia: ver [Certificados de una CA propia](#certificados-de-una-ca-propia) |
| Las sesiones de administración se pierden al reiniciar (fuera de Docker) | Falta `CHATBOT_SESSION_SECRET` |
| Todos los usuarios reciben `429` a la vez | Hay un proxy delante sin configurar `real_ip` (ver HTTPS) |
| El modelo dice que los documentos no contienen la respuesta | Revisa el índice y la recuperación con `app.diagnose` (abajo) |
| La cabecera muestra «Documentación no disponible» | `/api/v1/health` no alcanza el índice vectorial o el servicio de embeddings: el chat responde sin documentación y lo avisa en cada respuesta. Revisa `docker compose logs backend` («Estado: falla…») y la configuración de embeddings. El estado se comprueba como mucho una vez por minuto; el healthcheck de Docker no reinicia el contenedor por esto |

### Diagnóstico del índice y de la recuperación

`app.diagnose` solo lee: no modifica la base de datos ni el índice.

```bash
# ¿Están completos los fragmentos de un documento y coinciden sus vectores con los que
# calcula ahora el servicio de embeddings? (id o parte del nombre del fichero)
docker compose exec backend python -m app.diagnose index manual-empleado --batch-check -v

# Todos los documentos indexados (limitando los fragmentos que se vuelven a calcular)
docker compose exec backend python -m app.diagnose index --all --max-chunks 50

# Qué fragmentos se recuperan para una pregunta y cuáles usaría el chat
docker compose exec backend python -m app.diagnose query "¿cómo se solicitan las vacaciones?"

# En qué puesto quedan los fragmentos del documento que debería responder
docker compose exec backend python -m app.diagnose query "¿cómo se solicitan las vacaciones?" \
  --document manual-empleado

# Qué recuperaría el chat si la búsqueda se limita a un documento (se puede repetir)
docker compose exec backend python -m app.diagnose query "resúmeme el procedimiento" \
  --scope manual-empleado

# ¿Recorta el servidor de embeddings los fragmentos largos?
docker compose exec backend python -m app.diagnose index manual-empleado --truncation-check --max-chunks 10

# ¿Es fiable la estimación del tamaño del prompt con cada modelo? (últimos 30 días)
docker compose exec backend python -m app.diagnose tokens
```

`index` compara, por documento:

- los fragmentos que indica la base de datos, los que hay en el índice y los que se generan
  ahora a partir del fichero original;
- cada vector guardado con el que devuelve ahora el servicio de embeddings, enviando los
  fragmentos de uno en uno (similitud mínima esperada: 0,99);
- con `--batch-check`, los vectores calculados en lotes (tamaño de lote de la configuración de
  embeddings) con los calculados de uno en uno. Si difieren, el servidor de embeddings no es
  fiable con lotes: baja el tamaño de lote y pulsa *Reindexar todo*.

- con `--truncation-check`, si el servidor de embeddings ignora el final de los fragmentos:
  añade un texto de control al final de cada uno y comprueba si el vector cambia. Si no
  cambia, el servidor recorta los textos (contexto del modelo demasiado corto) y el script
  estima a partir de cuántos caracteres. En ese caso, baja el tamaño de fragmento por
  debajo de ese límite o amplía el contexto del modelo en el servidor, y reindexa.

También señala los vectores nulos, los textos distintos con el mismo vector, los fragmentos
con aspecto de índice (tabla de contenidos) y los documentos indexados con otra limpieza del
texto. Aplica la misma limpieza y el mismo título y sección que la indexación, así que los
vectores deben coincidir. Termina con código 2 si encuentra anomalías.

`query` busca la pregunta sin reformularla (el chat reescribe las preguntas de seguimiento) y
marca con ✓ los fragmentos que llegarían al modelo según el top-k, la puntuación mínima y el
máximo de contexto de la configuración de recuperación, y con el peso de los fragmentos de
índice aplicado. Con `--document` (se puede repetir)
clasifica todo el índice y muestra en qué puesto quedan los fragmentos de ese documento, con
la diferencia de score respecto al último fragmento que llega al modelo. Con `--scope` (se
puede repetir) busca solo en esos documentos, como el chat cuando se limita la búsqueda.
