"""Diagnóstico del índice vectorial y de la recuperación (ver issue #41).

Comprueba si los fragmentos indexados de un documento están completos y si sus vectores
coinciden con los que calcula ahora el servicio de embeddings, y muestra qué fragmentos se
recuperan para una pregunta.

Uso (desde `backend/`, o con `docker compose exec backend` en Docker):

    python -m app.diagnose index DOCUMENTO [DOCUMENTO ...] [--batch-check] [--truncation-check]
    python -m app.diagnose index --all [--max-chunks N]
    python -m app.diagnose query "pregunta" [--top N] [--document DOCUMENTO]

DOCUMENTO es el id del documento o parte de su nombre de fichero. Solo lee: no modifica la
base de datos ni el índice. Cada fragmento se vuelve a enviar al servicio de embeddings, de
uno en uno (y otra vez por cada comprobación opcional): en documentos grandes, limita la
prueba con `--max-chunks`.

Código de salida: 0 sin anomalías, 1 por error y 2 si se detecta alguna anomalía.
"""

import argparse
import asyncio
import math
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TextIO

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings, get_settings
from app.db.models import Document, DocumentKind, DocumentStatus
from app.db.session import build_engine, build_sessionmaker
from app.services.backends import build_vector_store, close_vector_store
from app.services.documents import document_file_path
from app.services.embeddings import EmbeddingError, Embeddings, build_embeddings
from app.services.ingestion.chunker import chunk_sections
from app.services.ingestion.parsers import ParseError, parse_document
from app.services.vectorstore import SearchHit, StoredChunk, VectorStore, VectorStoreError

# Por debajo de esta similitud, el vector guardado no es el que calcula ahora el servicio.
DEFAULT_MIN_SIMILARITY = 0.99
# Dos fragmentos distintos con vectores prácticamente idénticos indican un vector erróneo.
DUPLICATE_SIMILARITY = 0.9999
SNIPPET_CHARS = 120

# Líneas de un índice. Al extraer el texto de un PDF, el número de página puede quedar en la
# misma línea que el título o en una línea aparte (y los puntos de relleno, en cualquiera).
_TOC_LINE = re.compile(r"(?:\.{3,}|…+|\s)\s*\d{1,4}\s*$")  # «Vacaciones ..... 5»
_PAGE_NUMBER_LINE = re.compile(r"^[.…·\s]*\d{1,4}$")  # «5» o «..... 5»
_LEADER_LINE = re.compile(r"(?:\.{3,}|…+)\s*$")  # «Vacaciones .....»
TOC_MIN_LINES = 5
TOC_MIN_RATIO = 0.4

# Comprobación de recorte: texto sin relación que se añade al final de un fragmento. Si el
# vector no cambia, el servidor no lo ha leído: recorta los textos antes de ese punto.
TRUNCATION_PROBE = " ".join(
    [
        "Texto de control sin relación con el documento, añadido para comprobar si el",
        "servicio de embeddings tiene en cuenta el final de cada fragmento. Incluye palabras",
        "variadas: astronomía, cocina mediterránea, ferrocarriles, ajedrez, volcanes,",
        "orquestas sinfónicas, arrecifes de coral, satélites meteorológicos y molinos de viento.",
    ]
    * 2
)
TRUNCATION_SIMILARITY = 0.999


class DiagnoseError(Exception):
    """No se puede hacer el diagnóstico (p. ej. el documento no existe)."""


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Similitud coseno; 0 si alguno de los vectores es nulo o tiene otra dimensión."""
    if len(a) != len(b):
        return 0.0
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    if not norm or math.isnan(norm):
        return 0.0
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm


def looks_like_toc(text: str) -> bool:
    """True si el texto parece un índice (muchas líneas con número de página o relleno)."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) < TOC_MIN_LINES:
        return False
    matches = sum(
        1
        for line in lines
        if _TOC_LINE.search(line) or _PAGE_NUMBER_LINE.match(line) or _LEADER_LINE.search(line)
    )
    return matches / len(lines) >= TOC_MIN_RATIO


def _snippet(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET_CHARS else text[: SNIPPET_CHARS - 1].rstrip() + "…"


def _location(page: int | None, heading: str | None) -> str:
    if page is not None:
        return f"pág. {page}"
    return heading or "—"


@dataclass(slots=True)
class ChunkCheck:
    """Comparación del vector guardado de un fragmento con uno recién calculado."""

    chunk_index: int
    location: str
    similarity: float | None = None
    batch_similarity: float | None = None
    # Similitud entre el fragmento y el fragmento con texto añadido al final (≈1: recorte).
    probe_similarity: float | None = None
    toc: bool = False
    snippet: str = ""


@dataclass(slots=True)
class DocumentReport:
    """Resultado del diagnóstico de un documento."""

    document_id: str
    filename: str
    status: str
    embedding_model: str | None
    db_chunks: int
    stored_chunks: int = 0
    expected_chunks: int | None = None
    checked_chunks: int = 0
    # Caracteres a partir de los que el servidor de embeddings ignora el texto, si recorta.
    truncation_chars: int | None = None
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    chunks: list[ChunkCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


async def find_documents(
    sessionmaker: async_sessionmaker[AsyncSession], selectors: Sequence[str], all_documents: bool
) -> list[Document]:
    """Documentos por id o por parte del nombre (o todos los indexados con `all_documents`)."""
    async with sessionmaker() as session:
        if all_documents:
            result = await session.scalars(
                select(Document)
                .where(Document.status == DocumentStatus.READY.value)
                .order_by(Document.filename)
            )
            return list(result)
        found: dict[str, Document] = {}
        for selector in selectors:
            document = await session.get(Document, selector)
            matches = (
                [document]
                if document is not None
                else list(
                    await session.scalars(
                        select(Document).where(Document.filename.contains(selector))
                    )
                )
            )
            if not matches:
                raise DiagnoseError(f"No hay ningún documento con id o nombre «{selector}»")
            for match in matches:
                found[match.id] = match
        return sorted(found.values(), key=lambda d: d.filename)


def _check_vectors(report: DocumentReport, stored: Sequence[StoredChunk]) -> None:
    """Dimensión, normas y vectores duplicados entre fragmentos distintos."""
    dimensions = {len(chunk.vector) for chunk in stored}
    if len(dimensions) > 1:
        report.problems.append(f"Vectores de dimensiones distintas: {sorted(dimensions)}")
    for chunk in stored:
        norm = math.sqrt(sum(v * v for v in chunk.vector))
        if not norm or math.isnan(norm):
            report.problems.append(f"Fragmento {chunk.chunk_index}: vector nulo o no válido")
    if len(dimensions) != 1 or len(stored) < 2:
        return
    matrix = np.array([chunk.vector for chunk in stored], dtype=np.float64)
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    matrix = np.divide(matrix, norms, out=np.zeros_like(matrix), where=norms > 0)
    similarities = matrix @ matrix.T
    for i, j in zip(*np.nonzero(np.triu(similarities >= DUPLICATE_SIMILARITY, k=1)), strict=True):
        first, second = stored[int(i)], stored[int(j)]
        if first.text != second.text:
            report.problems.append(
                f"Fragmentos {first.chunk_index} y {second.chunk_index}: textos distintos "
                "con el mismo vector"
            )


async def _batch_vectors(
    embeddings: Embeddings, texts: Sequence[str], batch_size: int
) -> list[list[float]]:
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        vectors.extend(await embeddings.embed(list(texts[start : start + batch_size])))
    return vectors


async def _embed_one(embeddings: Embeddings, text: str) -> list[float]:
    return (await embeddings.embed([text]))[0]


async def estimate_truncation(embeddings: Embeddings, text: str, full: Sequence[float]) -> int:
    """Longitud mínima del principio de `text` cuyo vector ya es el del texto completo.

    Búsqueda binaria: unas 12 peticiones para un fragmento de 3.000 caracteres.
    """
    low, high = 1, len(text)
    while low < high:
        middle = (low + high) // 2
        if cosine(await _embed_one(embeddings, text[:middle]), full) >= TRUNCATION_SIMILARITY:
            high = middle
        else:
            low = middle + 1
    return low


async def diagnose_document(
    document: Document,
    settings: Settings,
    store: VectorStore,
    embeddings: Embeddings,
    *,
    min_similarity: float = DEFAULT_MIN_SIMILARITY,
    max_chunks: int | None = None,
    batch_check: bool = False,
    truncation_check: bool = False,
) -> DocumentReport:
    """Compara lo indexado de un documento con lo que se generaría ahora."""
    report = DocumentReport(
        document_id=document.id,
        filename=document.filename,
        status=document.status,
        embedding_model=document.embedding_model,
        db_chunks=document.chunk_count,
    )
    if document.status != DocumentStatus.READY.value:
        report.notes.append(f"El documento no está indexado (estado: {document.status})")
    if document.embedding_model and document.embedding_model != embeddings.model_name:
        report.problems.append(
            f"Indexado con «{document.embedding_model}» pero el modelo configurado es "
            f"«{embeddings.model_name}»: hay que reindexarlo"
        )

    stored = await store.document_chunks(document.id)
    report.stored_chunks = len(stored)
    if document.status == DocumentStatus.READY.value and len(stored) != document.chunk_count:
        report.problems.append(
            f"La base de datos indica {document.chunk_count} fragmentos y el índice tiene "
            f"{len(stored)}"
        )
    indices = [chunk.chunk_index for chunk in stored]
    if indices != list(range(len(stored))):
        missing = sorted(set(range(max(indices, default=-1) + 1)) - set(indices))
        report.problems.append(f"Faltan fragmentos en el índice: {missing[:20]}")

    # Fragmentos que generaría ahora el fichero original con la configuración actual.
    path = document_file_path(settings, document)
    if not path.exists():
        report.problems.append(f"No se encuentra el fichero original ({path})")
    else:
        try:
            sections = await asyncio.to_thread(parse_document, path, DocumentKind(document.kind))
        except ParseError as exc:
            report.problems.append(f"No se puede leer el original: {exc}")
        else:
            expected = chunk_sections(
                sections, settings.chunk_size_chars, settings.chunk_overlap_chars
            )
            report.expected_chunks = len(expected)
            stored_texts = [chunk.text for chunk in stored]
            if stored_texts != [chunk.text for chunk in expected]:
                report.problems.append(
                    f"El texto indexado no coincide con el del original ({len(expected)} "
                    f"fragmentos esperados): el índice está desactualizado o cambió el troceado"
                )

    toc = [chunk.chunk_index for chunk in stored if looks_like_toc(chunk.text)]
    if toc:
        report.notes.append(f"Fragmentos con aspecto de índice: {toc}")

    if not stored:
        return report
    _check_vectors(report, stored)

    sample = stored if max_chunks is None else stored[:max_chunks]
    if len(sample) < len(stored):
        report.notes.append(f"Solo se comprueban los {len(sample)} primeros fragmentos")
    try:
        # Uno a uno: el resultado no depende de cómo agrupe el servidor las peticiones.
        fresh = [await _embed_one(embeddings, chunk.text) for chunk in sample]
        batched = (
            await _batch_vectors(
                embeddings, [chunk.text for chunk in sample], settings.embedding_batch_size
            )
            if batch_check
            else None
        )
        probed = (
            [
                await _embed_one(embeddings, f"{chunk.text}\n\n{TRUNCATION_PROBE}")
                for chunk in sample
            ]
            if truncation_check
            else None
        )
    except EmbeddingError as exc:
        report.problems.append(f"El servicio de embeddings ha fallado: {exc}")
        return report

    report.checked_chunks = len(sample)
    low: list[int] = []
    batch_low: list[int] = []
    truncated: list[int] = []
    for position, chunk in enumerate(sample):
        check = ChunkCheck(
            chunk_index=chunk.chunk_index,
            location=_location(chunk.page, chunk.heading),
            similarity=cosine(chunk.vector, fresh[position]),
            toc=chunk.chunk_index in toc,
            snippet=_snippet(chunk.text),
        )
        if check.similarity is not None and check.similarity < min_similarity:
            low.append(chunk.chunk_index)
        if batched is not None:
            check.batch_similarity = cosine(batched[position], fresh[position])
            if check.batch_similarity < min_similarity:
                batch_low.append(chunk.chunk_index)
        if probed is not None:
            check.probe_similarity = cosine(probed[position], fresh[position])
            if check.probe_similarity >= TRUNCATION_SIMILARITY:
                truncated.append(chunk.chunk_index)
        report.chunks.append(check)
    if low:
        report.problems.append(
            f"{len(low)} fragmentos con un vector guardado distinto del que se calcula ahora "
            f"(similitud < {min_similarity}): {low[:20]}"
        )
    if batch_low:
        report.problems.append(
            f"El servicio devuelve vectores distintos en lotes de "
            f"{settings.embedding_batch_size} que de uno en uno en {len(batch_low)} "
            f"fragmentos: {batch_low[:20]}"
        )
    if truncated:
        # Se estima el límite con el fragmento recortado más largo.
        longest = max(
            (chunk for chunk in sample if chunk.chunk_index in truncated),
            key=lambda chunk: len(chunk.text),
        )
        try:
            full = fresh[sample.index(longest)]
            report.truncation_chars = await estimate_truncation(embeddings, longest.text, full)
            limit = f"unos {report.truncation_chars} caracteres"
        except EmbeddingError:
            limit = "una longitud que no se ha podido estimar"
        report.problems.append(
            f"El servicio de embeddings recorta los textos: ignora lo que hay a partir de "
            f"{limit} en {len(truncated)} de {len(sample)} fragmentos ({truncated[:20]}). "
            f"Baja CHATBOT_CHUNK_SIZE_CHARS por debajo de ese límite o amplía el contexto del "
            f"modelo de embeddings en el servidor, y reindexa"
        )
    return report


@dataclass(slots=True)
class QueryHit:
    """Fragmento recuperado para una pregunta y si el chat lo usaría."""

    rank: int
    hit: SearchHit
    used: bool
    reason: str
    toc: bool


async def diagnose_query(
    query: str,
    settings: Settings,
    store: VectorStore,
    embeddings: Embeddings,
    top: int,
    full_ranking: bool = False,
) -> list[QueryHit]:
    """Fragmentos más parecidos a la pregunta, con los criterios del `Retriever`.

    Con `full_ranking` se clasifica todo el índice (para ver en qué puesto queda un
    documento concreto); si no, solo los `top` primeros.
    """
    [vector] = await embeddings.embed([query])
    limit = await store.count() if full_ranking else max(top, settings.rag_top_k)
    if limit == 0:
        return []
    hits = sorted(await store.search(vector, limit), key=lambda h: h.score, reverse=True)
    result = []
    used_chars = 0
    used_count = 0
    stopped: str | None = None
    for rank, hit in enumerate(hits, start=1):
        reason = ""
        if stopped is None:
            if rank > settings.rag_top_k:
                stopped = f"fuera de los {settings.rag_top_k} primeros (RAG_TOP_K)"
            elif hit.score < settings.rag_min_score:
                stopped = f"por debajo de RAG_MIN_SCORE ({settings.rag_min_score})"
            elif used_count and used_chars + len(hit.text) > settings.rag_max_context_chars:
                stopped = f"supera RAG_MAX_CONTEXT_CHARS ({settings.rag_max_context_chars})"
        used = stopped is None
        if used:
            used_chars += len(hit.text)
            used_count += 1
        else:
            reason = stopped or ""
        result.append(QueryHit(rank, hit, used, reason, looks_like_toc(hit.text)))
    return result


def print_document_report(report: DocumentReport, out: TextIO, verbose: bool) -> None:
    print(f"\n== {report.filename} ({report.document_id})", file=out)
    print(
        f"   estado: {report.status} · modelo: {report.embedding_model or '—'} · "
        f"fragmentos: BD {report.db_chunks}, índice {report.stored_chunks}, "
        f"esperados {report.expected_chunks if report.expected_chunks is not None else '—'}",
        file=out,
    )
    similarities = [c.similarity for c in report.chunks if c.similarity is not None]
    if similarities:
        print(
            f"   similitud guardado/actual ({report.checked_chunks} fragmentos): "
            f"mín {min(similarities):.4f}, media {sum(similarities) / len(similarities):.4f}",
            file=out,
        )
    batch = [c.batch_similarity for c in report.chunks if c.batch_similarity is not None]
    if batch:
        print(
            f"   similitud lote/individual: mín {min(batch):.4f}, "
            f"media {sum(batch) / len(batch):.4f}",
            file=out,
        )
    for note in report.notes:
        print(f"   · {note}", file=out)
    for problem in report.problems:
        print(f"   ✗ {problem}", file=out)
    if report.ok:
        print("   ✓ Sin anomalías", file=out)
    if verbose:
        for check in report.chunks:
            extra = ""
            if check.batch_similarity is not None:
                extra += f" lote {check.batch_similarity:.4f}"
            if check.probe_similarity is not None:
                extra += f" recorte {check.probe_similarity:.4f}"
            print(
                f"     #{check.chunk_index:<4} {check.location:<12} "
                f"{check.similarity or 0:.4f}{extra}{' [índice]' if check.toc else ''}  "
                f"{check.snippet}",
                file=out,
            )


def _print_hit(item: QueryHit, out: TextIO, reason: bool = True) -> None:
    hit = item.hit
    mark = "✓" if item.used else "·"
    location = _location(hit.page, hit.heading)
    print(
        f"{mark} {item.rank:>3}. {hit.score:.4f}  {hit.filename} · {location} · "
        f"#{hit.chunk_index}{' [índice]' if item.toc else ''}",
        file=out,
    )
    print(f"        {_snippet(hit.text)}", file=out)
    if reason and item.reason:
        print(f"        no se usa: {item.reason}", file=out)


def print_query_report(
    query: str,
    hits: Sequence[QueryHit],
    out: TextIO,
    top: int = 10,
    documents: Sequence[Document] = (),
) -> None:
    print(f"Pregunta: {query}", file=out)
    print("(sin reformular: el chat reescribe las preguntas de seguimiento)\n", file=out)
    if not hits:
        print("El índice no devuelve ningún fragmento.", file=out)
        return
    shown = hits[:top]
    for item in shown:
        _print_hit(item, out)
    used = [item for item in hits if item.used]
    toc_used = sum(1 for item in used if item.toc)
    print(f"\nEl chat usaría {len(used)} fragmentos; {toc_used} con aspecto de índice.", file=out)

    for document in documents:
        own = [item for item in hits if item.hit.document_id == document.id]
        print(f"\n== {document.filename} ({document.id})", file=out)
        if not own:
            print("   El documento no tiene fragmentos en el índice.", file=out)
            continue
        best = own[0]
        print(
            f"   {len(own)} fragmentos; el mejor queda en el puesto {best.rank} de {len(hits)} "
            f"(score {best.hit.score:.4f})",
            file=out,
        )
        if used and not best.used:
            last = used[-1]
            print(
                f"   El último fragmento que llega al modelo es el {last.rank} "
                f"(score {last.hit.score:.4f}): faltan {last.hit.score - best.hit.score:.4f}",
                file=out,
            )
        toc = sum(1 for item in own if item.toc)
        if toc:
            print(f"   {toc} de sus fragmentos tienen aspecto de índice", file=out)
        print(f"   Sus {min(top, len(own))} mejores fragmentos:", file=out)
        for item in own[:top]:
            _print_hit(item, out, reason=False)


async def run(args: argparse.Namespace, settings: Settings, out: TextIO) -> int:
    engine = build_engine(settings.resolved_database_url)
    store = build_vector_store(settings)
    embeddings = build_embeddings(settings)
    try:
        if args.command == "query":
            selected = (
                await find_documents(build_sessionmaker(engine), args.document, False)
                if args.document
                else []
            )
            hits = await diagnose_query(
                args.question, settings, store, embeddings, args.top, full_ranking=bool(selected)
            )
            print_query_report(args.question, hits, out, args.top, selected)
            return 0
        documents = await find_documents(build_sessionmaker(engine), args.documents, args.all)
        if not documents:
            print("No hay documentos indexados.", file=out)
            return 0
        print(
            f"Modelo de embeddings: {embeddings.model_name} · índice: {settings.vector_store} · "
            f"lotes de {settings.embedding_batch_size}",
            file=out,
        )
        reports = []
        for document in documents:
            report = await diagnose_document(
                document,
                settings,
                store,
                embeddings,
                min_similarity=args.min_similarity,
                max_chunks=args.max_chunks,
                batch_check=args.batch_check,
                truncation_check=args.truncation_check,
            )
            print_document_report(report, out, args.verbose)
            reports.append(report)
        failed = sum(1 for r in reports if not r.ok)
        print(f"\n{len(reports)} documentos revisados, {failed} con anomalías.", file=out)
        return 2 if failed else 0
    finally:
        await close_vector_store(store)
        await engine.dispose()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.diagnose", description=(__doc__ or "").split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    index = commands.add_parser("index", help="Comprueba los fragmentos y vectores indexados")
    index.add_argument("documents", nargs="*", help="Id o parte del nombre del documento")
    index.add_argument("--all", action="store_true", help="Todos los documentos indexados")
    index.add_argument(
        "--batch-check",
        action="store_true",
        help="Compara también los vectores calculados en lotes con los calculados de uno en uno",
    )
    index.add_argument(
        "--truncation-check",
        action="store_true",
        help="Comprueba si el servicio de embeddings ignora el final de los fragmentos",
    )
    index.add_argument(
        "--min-similarity",
        type=float,
        default=DEFAULT_MIN_SIMILARITY,
        help=f"Similitud mínima esperada (por defecto {DEFAULT_MIN_SIMILARITY})",
    )
    index.add_argument("--max-chunks", type=int, help="Comprueba solo los N primeros fragmentos")
    index.add_argument("-v", "--verbose", action="store_true", help="Detalle por fragmento")
    query = commands.add_parser(
        "query", help="Muestra los fragmentos recuperados para una pregunta"
    )
    query.add_argument("question")
    query.add_argument("--top", type=int, default=10, help="Resultados a mostrar (por defecto 10)")
    query.add_argument(
        "--document",
        action="append",
        metavar="DOCUMENTO",
        help="Muestra en qué puesto quedan los fragmentos de este documento (se puede repetir)",
    )
    return parser


def main(
    argv: Sequence[str] | None = None, settings: Settings | None = None, out: TextIO | None = None
) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "index" and not args.documents and not args.all:
        parser.error("indica uno o más documentos, o --all")
    out = out or sys.stdout
    try:
        return asyncio.run(run(args, settings or get_settings(), out))
    except (DiagnoseError, EmbeddingError, VectorStoreError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
