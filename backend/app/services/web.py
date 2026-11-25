"""Descarga de documentos por su dirección web (#78).

El servidor descarga desde la red interna o la DMZ: no se pueden bloquear las direcciones
privadas (los documentos de la intranet son el caso de uso), así que la descarga se limita:

- solo `http` y `https`, sin usuario ni contraseña en la dirección (no hay autenticación);
- solo la administración puede añadir direcciones;
- tiempo máximo para toda la descarga y tamaño máximo (el de subida), comprobado mientras
  se descarga;
- como mucho `MAX_REDIRECTS` redirecciones, y el destino de cada una se comprueba como la
  dirección inicial;
- lista opcional de dominios permitidos (configuración «Documentos web»).

Las páginas HTML se reducen a su contenido principal (sin menús, cabeceras, pies, avisos de
cookies…) con trafilatura y se guardan como Markdown, con sus encabezados.
"""

import asyncio
import re
import warnings
from dataclasses import dataclass
from pathlib import PurePosixPath
from urllib.parse import unquote, urljoin, urlsplit

import httpx

with warnings.catch_warnings():
    # trafilatura importa urllib3, que avisa de que falta el soporte SOCKS (no se usa: las
    # descargas las hace httpx).
    warnings.filterwarnings("ignore", message="SOCKS support in urllib3")
    import trafilatura

from app.core.runtime_config import WebConfig
from app.db.models import DocumentKind
from app.services.ingestion.parsers import kind_for_filename, parse_html
from app.services.ingestion.titles import clean_title, html_title

MAX_REDIRECTS = 5
READ_CHUNK_BYTES = 64 * 1024
# Cliente genérico: no identifica a la organización ni a quien lo usa.
USER_AGENT = "Mozilla/5.0 (compatible; chatbot-rag; documentos)"
MAX_URL_LENGTH = 2048

# Tipo de documento según el Content-Type (sin parámetros).
_CONTENT_TYPES: dict[str, DocumentKind] = {
    "text/html": DocumentKind.WEB,
    "application/xhtml+xml": DocumentKind.WEB,
    "application/pdf": DocumentKind.PDF,
    "text/markdown": DocumentKind.MARKDOWN,
    "text/x-markdown": DocumentKind.MARKDOWN,
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": DocumentKind.DOCX,
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        DocumentKind.PPTX
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": DocumentKind.XLSX,
    "application/vnd.oasis.opendocument.text": DocumentKind.ODT,
    "application/vnd.oasis.opendocument.presentation": DocumentKind.ODP,
    "application/vnd.oasis.opendocument.spreadsheet": DocumentKind.ODS,
}
# Extensión con la que se nombra el documento según su tipo.
_EXTENSIONS: dict[DocumentKind, str] = {
    DocumentKind.WEB: ".html",
    DocumentKind.PDF: ".pdf",
    DocumentKind.MARKDOWN: ".md",
    DocumentKind.DOCX: ".docx",
    DocumentKind.PPTX: ".pptx",
    DocumentKind.XLSX: ".xlsx",
    DocumentKind.ODT: ".odt",
    DocumentKind.ODP: ".odp",
    DocumentKind.ODS: ".ods",
}
_CHARSET = re.compile(r"charset\s*=\s*\"?([\w.:-]+)", re.IGNORECASE)


class WebError(Exception):
    """La dirección no se puede usar o la descarga falló (mensaje para la administración)."""


class NotFoundError(WebError):
    """La dirección no existe (404 o 410): la actualización periódica la cuenta (#79)."""


@dataclass(frozen=True, slots=True)
class Download:
    """Resultado de una descarga."""

    # Dirección final, tras las redirecciones.
    url: str
    content_type: str
    body: bytes
    etag: str | None = None
    last_modified: str | None = None


def check_url(url: str, config: WebConfig) -> str:
    """La dirección, sin espacios; error si no se puede descargar."""
    url = url.strip()
    if len(url) > MAX_URL_LENGTH:
        raise WebError("La dirección es demasiado larga")
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https"):
        raise WebError("Solo se admiten direcciones http:// o https://")
    if not parts.hostname:
        raise WebError("La dirección no es válida")
    if parts.username or parts.password:
        raise WebError("No se admiten direcciones con usuario o contraseña")
    host = parts.hostname.lower().rstrip(".")
    domains = config.domains
    if domains and not any(host == d or host.endswith(f".{d}") for d in domains):
        raise WebError(f"El dominio {host} no está entre los permitidos")
    return url


class WebFetcher:
    """Descarga documentos. `transport` permite simular el servidor en los tests."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def fetch(
        self,
        url: str,
        config: WebConfig,
        max_bytes: int,
        headers: dict[str, str] | None = None,
    ) -> Download:
        """Descarga `url` (comprobada con `check_url`) siguiendo las redirecciones."""
        try:
            async with asyncio.timeout(config.timeout_seconds):
                return await self._fetch(url, config, max_bytes, headers or {})
        except TimeoutError as exc:
            raise WebError(
                f"El servidor no respondió a tiempo ({config.timeout_seconds} s)"
            ) from exc
        except httpx.HTTPError as exc:
            raise WebError(f"No se pudo conectar con el servidor: {_describe(exc)}") from exc

    async def _fetch(
        self, url: str, config: WebConfig, max_bytes: int, headers: dict[str, str]
    ) -> Download:
        async with httpx.AsyncClient(
            transport=self._transport,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, **headers},
            timeout=config.timeout_seconds,
        ) as client:
            current = check_url(url, config)
            for _ in range(MAX_REDIRECTS + 1):
                async with client.stream("GET", current) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise WebError("Redirección sin destino")
                        current = check_url(urljoin(current, location), config)
                        continue
                    if response.status_code in (404, 410):
                        raise NotFoundError(f"No encontrado ({response.status_code})")
                    if not response.is_success:
                        raise WebError(f"El servidor respondió {response.status_code}")
                    body = await _read(response, max_bytes)
                    return Download(
                        url=current,
                        content_type=response.headers.get("content-type", ""),
                        body=body,
                        etag=response.headers.get("etag"),
                        last_modified=response.headers.get("last-modified"),
                    )
            raise WebError(f"Demasiadas redirecciones (más de {MAX_REDIRECTS})")


async def _read(response: httpx.Response, max_bytes: int) -> bytes:
    too_big = WebError(f"El documento supera el tamaño máximo ({max_bytes // (1024 * 1024)} MB)")
    declared = response.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise too_big
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes(READ_CHUNK_BYTES):
        size += len(chunk)
        if size > max_bytes:
            raise too_big
        chunks.append(chunk)
    return b"".join(chunks)


def _describe(exc: httpx.HTTPError) -> str:
    return str(exc) or exc.__class__.__name__


# --- Tipo, nombre y contenido ----------------------------------------------------------


def _url_name(url: str) -> str:
    """Último tramo de la ruta de la dirección (o el servidor, si no tiene ruta)."""
    parts = urlsplit(url)
    name = unquote(PurePosixPath(parts.path).name)
    return name or (parts.hostname or "documento")


def document_kind(download: Download) -> DocumentKind:
    """Tipo según el Content-Type o, si no lo indica, la extensión de la dirección."""
    content_type = download.content_type.split(";")[0].strip().lower()
    kind = _CONTENT_TYPES.get(content_type) or kind_for_filename(_url_name(download.url))
    if kind is DocumentKind.HTML:
        kind = DocumentKind.WEB
    if kind is None:
        raise WebError(
            f"Tipo de documento no admitido ({content_type or 'sin tipo'}): se aceptan páginas "
            "web, PDF, Markdown, Word, PowerPoint, Excel y OpenDocument"
        )
    return kind


def document_name(download: Download, kind: DocumentKind) -> str:
    """Nombre del documento: el último tramo de la dirección, con la extensión de su tipo."""
    name = _url_name(download.url)
    extension = _EXTENSIONS[kind]
    if PurePosixPath(name).suffix.lower() not in {extension, ".htm"}:
        name = f"{name}{extension}"
    return name


def decode_html(download: Download) -> str:
    """Texto de la página, con la codificación del Content-Type o, si no la indica, UTF-8
    (o Windows-1252 si no lo es)."""
    match = _CHARSET.search(download.content_type)
    if match:
        try:
            return download.body.decode(match.group(1), errors="replace")
        except LookupError:
            pass
    try:
        return download.body.decode("utf-8-sig")
    except UnicodeDecodeError:
        return download.body.decode("cp1252", errors="replace")


def page_title(html: str) -> str | None:
    """Título de la página: el de trafilatura (sin el nombre del sitio) o el de `<title>`."""
    metadata = trafilatura.extract_metadata(html)
    title = clean_title(metadata.title) if metadata is not None else None
    return title or html_title(html)


def html_to_markdown(html: str) -> str:
    """Contenido principal de la página como Markdown (con sus encabezados y tablas).

    Si trafilatura no encuentra contenido, se usa todo el texto visible de la página.
    """
    markdown = trafilatura.extract(
        html,
        output_format="markdown",
        include_tables=True,
        include_formatting=True,
        include_links=False,
        include_images=False,
        include_comments=False,
        favor_recall=True,
    )
    if markdown and markdown.strip():
        return markdown.strip() + "\n"
    sections = parse_html(html)
    return "\n\n".join(
        f"# {section.heading}\n\n{section.text}" if section.heading else section.text
        for section in sections
    )
