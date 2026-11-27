"""Evaluación reproducible de la recuperación (RAG).

Mide, para un conjunto de preguntas con el documento esperado, si la búsqueda encuentra
la fuente correcta con distintas configuraciones de troceado, top-k y umbral de
similitud, y recomienda la mejor. Usa el proveedor de embeddings configurado en `.env`,
así que sirve para calibrar el modelo real (p. ej. el de LiteLLM).

Uso (desde `backend/`):

    uv run python -m app.evaluation --dataset eval/ejemplo/preguntas.json \\
        --documents eval/ejemplo/documentos --chunk-sizes 1000,3000 --top-k 3,5

Sin `--documents` se evalúan los documentos subidos a la aplicación (`data/`).

Mientras trabaja muestra el progreso en la salida de errores (el informe va a la salida
estándar, así que se puede redirigir a un fichero sin mezclarse).
"""

import argparse
import asyncio
import json
import sys
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import TextIO

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.core.runtime_config import IndexingConfig
from app.db.models import Document, DocumentKind, DocumentStatus
from app.db.session import build_engine, build_sessionmaker
from app.services.condense import condense_question
from app.services.documents import document_file_path
from app.services.embeddings import Embeddings, build_embeddings
from app.services.ingestion.parsers import ParseError, kind_for_filename, parse_document
from app.services.ingestion.preprocess import (
    build_chunks,
    document_title,
    embedding_texts,
    preprocessing_signature,
)
from app.services.llm import ChatMessage, LLMClient, build_llm_client
from app.services.rag import rank_hits
from app.services.runtime_config import load_runtime_config
from app.services.vectorstore import ChromaVectorStore, SearchHit

# --- Conjunto de evaluación -------------------------------------------------------------


class ExpectedSource(BaseModel):
    """Fuente que debería recuperarse. `pagina` y `seccion` son opcionales."""

    model_config = ConfigDict(extra="forbid")

    fichero: str = Field(min_length=1)
    pagina: int | None = Field(default=None, ge=1)
    seccion: str | None = None

    def matches(self, hit: SearchHit) -> bool:
        if hit.filename.lower() != self.fichero.lower():
            return False
        if self.pagina is not None and hit.page != self.pagina:
            return False
        return self.seccion is None or self.seccion.lower() in (hit.heading or "").lower()


class EvaluationCase(BaseModel):
    """Pregunta con sus fuentes esperadas (lista vacía: no debe encontrar nada)."""

    model_config = ConfigDict(extra="forbid")

    pregunta: str = Field(min_length=1)
    esperado: list[ExpectedSource] = []
    # Preguntas anteriores del usuario, para evaluar la reformulación (--condensar).
    historial: list[str] = []


class EvaluationDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    preguntas: list[EvaluationCase] = Field(min_length=1)


def load_dataset(path: Path) -> list[EvaluationCase]:
    """Lee y valida el conjunto de preguntas (JSON)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return EvaluationDataset.model_validate(data).preguntas


# --- Documentos -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SourceDocument:
    path: Path
    filename: str
    kind: DocumentKind


def documents_from_folder(folder: Path) -> list[SourceDocument]:
    """Ficheros admitidos de una carpeta (no recursivo)."""
    documents = []
    for path in sorted(folder.iterdir()):
        kind = kind_for_filename(path.name)
        if path.is_file() and kind is not None:
            documents.append(SourceDocument(path, path.name, kind))
    return documents


async def documents_from_app(settings: Settings) -> list[SourceDocument]:
    """Documentos indexados en la aplicación (base de datos y carpeta de subidas)."""
    engine = build_engine(settings.resolved_database_url)
    try:
        async with build_sessionmaker(engine)() as session:
            rows = await session.scalars(
                select(Document).where(Document.status == DocumentStatus.READY.value)
            )
            documents = []
            for row in rows:
                path = document_file_path(settings, row)
                if path.is_file():
                    documents.append(SourceDocument(path, row.filename, DocumentKind(row.kind)))
            return documents
    finally:
        await engine.dispose()


# --- Métricas ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RankedHit:
    score: float
    relevant: bool


@dataclass(frozen=True, slots=True)
class CaseResult:
    question: str
    query: str
    answerable: bool
    hits: list[RankedHit]


@dataclass(frozen=True, slots=True)
class Metrics:
    top_k: int
    threshold: float
    hit_rate: float  # preguntas con la fuente correcta entre las k primeras (sin umbral)
    mrr: float  # media de 1/posición de la primera fuente correcta (sin umbral)
    recall: float  # preguntas con la fuente correcta por encima del umbral
    precision: float  # fuentes mostradas que son correctas (preguntas con respuesta)
    false_positive_rate: float  # preguntas sin respuesta a las que se muestran fuentes
    utility: float  # criterio para recomendar: recall * (precisión o 1 - falsos positivos)


def _mean(values: Sequence[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compute_metrics(results: Sequence[CaseResult], top_k: int, threshold: float) -> Metrics:
    answerable = [r for r in results if r.answerable]
    unanswerable = [r for r in results if not r.answerable]

    hit_rate, mrr, recall, precisions = [], [], [], []
    for result in answerable:
        top = result.hits[:top_k]
        rank = next((i for i, h in enumerate(top, start=1) if h.relevant), None)
        hit_rate.append(1.0 if rank else 0.0)
        mrr.append(1.0 / rank if rank else 0.0)
        shown = [h for h in top if h.score >= threshold]
        recall.append(1.0 if any(h.relevant for h in shown) else 0.0)
        if shown:
            precisions.append(sum(h.relevant for h in shown) / len(shown))

    false_positives = [
        1.0 if any(h.score >= threshold for h in r.hits[:top_k]) else 0.0 for r in unanswerable
    ]
    recall_value = _mean(recall)
    precision_value = _mean(precisions)
    fpr = _mean(false_positives)
    quality = (1 - fpr) if unanswerable else precision_value
    return Metrics(
        top_k=top_k,
        threshold=round(threshold, 4),
        hit_rate=_mean(hit_rate),
        mrr=_mean(mrr),
        recall=recall_value,
        precision=precision_value,
        false_positive_rate=fpr,
        utility=recall_value * quality,
    )


def recommend(results: Sequence[CaseResult], top_k: int, thresholds: Sequence[float]) -> Metrics:
    """Umbral con mayor utilidad; en caso de empate, el más alto (menos ruido)."""
    candidates = [compute_metrics(results, top_k, t) for t in thresholds]
    return max(candidates, key=lambda m: (round(m.utility, 6), m.threshold))


# --- Ejecución --------------------------------------------------------------------------

Progress = Callable[[str], None]


def _no_progress(message: str) -> None:
    pass


def format_duration(seconds: float) -> str:
    """Duración legible: `45 s`, `3 min 05 s`, `1 h 02 min`."""
    total = round(seconds)
    if total < 60:
        return f"{total} s"
    minutes, secs = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} min {secs:02d} s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} h {minutes:02d} min"


# Sin preprocesado del texto (el troceado se sustituye por el de cada configuración).
_NO_PREPROCESSING = IndexingConfig(
    upload_max_mb=1,
    chunk_size_chars=3000,
    chunk_overlap_chars=0,
    strip_repeated_lines=False,
    repeated_line_min_ratio=1,
    clean_toc_leaders=False,
    context_prefix=False,
    pdf_headings=False,
)


@dataclass(frozen=True, slots=True)
class ChunkConfig:
    size: int
    overlap: int


@dataclass
class ConfigReport:
    chunk_size: int
    chunk_overlap: int
    chunks: int
    best: Metrics
    by_threshold: list[Metrics] = field(default_factory=list)
    cases: list[CaseResult] = field(default_factory=list)


@dataclass
class EvaluationReport:
    embedding_model: str
    documents: list[str]
    questions: int
    unanswerable: int
    configs: list[ConfigReport]
    skipped_documents: dict[str, str] = field(default_factory=dict)
    # Preprocesado del texto (`preprocessing_signature`) y peso de los fragmentos de índice.
    preprocessing: str = "ninguno"
    toc_score_factor: float = 1.0

    @property
    def best(self) -> ConfigReport:
        return max(self.configs, key=lambda c: (round(c.best.utility, 6), c.best.mrr))


async def _build_index(
    documents: Sequence[SourceDocument],
    indexing: IndexingConfig,
    embeddings: Embeddings,
    skipped: dict[str, str],
    progress: Progress,
    label: str,
) -> tuple[ChromaVectorStore, int]:
    store = ChromaVectorStore(None, collection=f"eval-{uuid.uuid4().hex}")
    total = 0
    started = time.monotonic()
    for number, document in enumerate(documents, start=1):
        try:
            sections = await asyncio.to_thread(parse_document, document.path, document.kind)
        except ParseError as exc:
            skipped[document.filename] = str(exc)
            progress(f"{label} documento {number}/{len(documents)}: omitido {document.filename}")
            continue
        chunks = build_chunks(sections, indexing, document.kind)
        vectors = await embeddings.embed(
            embedding_texts(chunks, document_title(document.filename), indexing)
        )
        await store.add(document.filename, document.filename, chunks, vectors)
        total += len(chunks)
        elapsed = time.monotonic() - started
        remaining = elapsed / number * (len(documents) - number)
        eta = f" · quedan unos {format_duration(remaining)}" if number < len(documents) else ""
        progress(
            f"{label} documento {number}/{len(documents)} ({len(chunks)} fragmentos, "
            f"{total} en total) · {format_duration(elapsed)}{eta}"
        )
    return store, total


async def _queries(cases: Sequence[EvaluationCase], llm: LLMClient | None) -> list[str]:
    queries = []
    for case in cases:
        if llm is not None and case.historial:
            previous = [ChatMessage("user", question) for question in case.historial]
            queries.append(await condense_question(llm, previous, case.pregunta))
        else:
            queries.append(case.pregunta)
    return queries


async def run_evaluation(
    cases: Sequence[EvaluationCase],
    documents: Sequence[SourceDocument],
    configs: Sequence[ChunkConfig],
    top_ks: Sequence[int],
    thresholds: Sequence[float],
    embeddings: Embeddings,
    llm: LLMClient | None = None,
    progress: Progress = _no_progress,
    indexing: IndexingConfig | None = None,
    toc_score_factor: float = 1.0,
) -> EvaluationReport:
    """Evalúa cada configuración de troceado con todos los top-k y umbrales.

    `indexing` indica el preprocesado del texto, como al indexar (sin él, no se aplica);
    su troceado se sustituye por el de cada configuración. `toc_score_factor` resta peso
    a los fragmentos de índice, como el chat. `progress` recibe mensajes de avance
    (documento a documento) para mostrarlos.
    """
    if not documents:
        raise ValueError("No hay documentos que evaluar")
    started = time.monotonic()
    with_history = sum(1 for case in cases if case.historial)
    if llm is not None and with_history:
        progress(f"Reformulando con el modelo {with_history} preguntas con historial…")
    queries = await _queries(cases, llm)
    progress(f"Calculando los embeddings de {len(queries)} preguntas con {embeddings.model_name}…")
    query_vectors = await embeddings.embed(queries)
    max_k = max(top_ks)
    # Con fragmentos de índice penalizados se piden más candidatos, como el chat.
    limit = max_k if toc_score_factor >= 1 else max_k * 2
    base = _NO_PREPROCESSING if indexing is None else indexing
    skipped: dict[str, str] = {}
    reports = []
    for number, config in enumerate(configs, start=1):
        label = f"[{number}/{len(configs)}] fragmento {config.size}, solape {config.overlap}:"
        progress(f"{label} indexando {len(documents)} documentos…")
        config_started = time.monotonic()
        chunk_indexing = base.model_copy(
            update={"chunk_size_chars": config.size, "chunk_overlap_chars": config.overlap}
        )
        store, total = await _build_index(
            documents, chunk_indexing, embeddings, skipped, progress, label
        )
        progress(
            f"{label} {total} fragmentos indexados en "
            f"{format_duration(time.monotonic() - config_started)}; buscando…"
        )
        try:
            results = []
            for case, query, vector in zip(cases, queries, query_vectors, strict=True):
                found = await store.search(vector, limit)
                hits = rank_hits(found, toc_score_factor)[:max_k]
                ranked = [
                    RankedHit(h.score, any(e.matches(h) for e in case.esperado)) for h in hits
                ]
                results.append(CaseResult(case.pregunta, query, bool(case.esperado), ranked))
        finally:
            store.drop()
        options = [recommend(results, k, thresholds) for k in top_ks]
        best = max(options, key=lambda m: (round(m.utility, 6), m.mrr, -m.top_k))
        reports.append(
            ConfigReport(
                chunk_size=config.size,
                chunk_overlap=config.overlap,
                chunks=total,
                best=best,
                by_threshold=[compute_metrics(results, best.top_k, t) for t in thresholds],
                cases=results,
            )
        )
    progress(f"Evaluación terminada en {format_duration(time.monotonic() - started)}")
    return EvaluationReport(
        embedding_model=embeddings.model_name,
        documents=[d.filename for d in documents],
        questions=len(cases),
        unanswerable=sum(1 for c in cases if not c.esperado),
        configs=reports,
        skipped_documents=skipped,
        preprocessing=preprocessing_signature(base) if indexing is not None else "ninguno",
        toc_score_factor=toc_score_factor,
    )


# --- Informe ----------------------------------------------------------------------------


def _pct(value: float) -> str:
    return f"{value * 100:.0f} %"


def format_report(report: EvaluationReport) -> str:
    """Informe legible en Markdown."""
    lines = [
        "# Evaluación de la recuperación",
        "",
        f"- Modelo de embeddings: `{report.embedding_model}`",
        f"- Preprocesado: `{report.preprocessing}` · peso de los fragmentos de índice: "
        f"{report.toc_score_factor:g}",
        f"- Documentos: {len(report.documents)} · Preguntas: {report.questions} "
        f"({report.unanswerable} sin respuesta en los documentos)",
    ]
    for name, reason in report.skipped_documents.items():
        lines.append(f"- ⚠️ Omitido `{name}`: {reason}")
    lines += [
        "",
        "## Configuraciones",
        "",
        "| Fragmento | Solape | Nº fragm. | top-k | Umbral | Acierto@k | MRR | Recall | "
        "Precisión | Falsos positivos |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for c in report.configs:
        m = c.best
        lines.append(
            f"| {c.chunk_size} | {c.chunk_overlap} | {c.chunks} | {m.top_k} | {m.threshold:.2f} | "
            f"{_pct(m.hit_rate)} | {m.mrr:.2f} | {_pct(m.recall)} | {_pct(m.precision)} | "
            f"{_pct(m.false_positive_rate)} |"
        )
    best = report.best
    lines += [
        "",
        "## Recomendación",
        "",
        "Valores para Administración → Configuración:",
        "",
        f"- Indexación · tamaño de fragmento: {best.chunk_size}",
        f"- Indexación · solapamiento: {best.chunk_overlap}",
        f"- Recuperación · top-k: {best.best.top_k}",
        f"- Recuperación · puntuación mínima: {best.best.threshold:.2f}",
        "",
        "Si cambias el troceado, pulsa «Reindexar todo» en Administración → Documentos.",
        "",
        f"### Umbrales (fragmento {best.chunk_size}, top-k {best.best.top_k})",
        "",
        "| Umbral | Recall | Precisión | Falsos positivos |",
        "|---|---|---|---|",
    ]
    for m in best.by_threshold:
        lines.append(
            f"| {m.threshold:.2f} | {_pct(m.recall)} | {_pct(m.precision)} | "
            f"{_pct(m.false_positive_rate)} |"
        )
    failures = [r for r in best.cases if r.answerable and not any(h.relevant for h in r.hits)]
    if failures:
        lines += ["", "### Preguntas sin la fuente esperada entre los resultados", ""]
        for r in failures:
            rewritten = f" (buscada como «{r.query}»)" if r.query != r.question else ""
            lines.append(f"- {r.question}{rewritten}")
    return "\n".join(lines) + "\n"


# --- Línea de comandos ------------------------------------------------------------------


def _int_list(value: str) -> list[int]:
    return [int(v) for v in value.split(",") if v.strip()]


def _thresholds(value: str) -> list[float]:
    """`0.1,0.3` o un rango `inicio:fin:paso` (incluye el fin)."""
    if ":" in value:
        start, stop, step = (float(v) for v in value.split(":"))
        count = round((stop - start) / step) + 1
        return [round(start + i * step, 4) for i in range(count)]
    return [float(v) for v in value.split(",") if v.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation",
        description="Evalúa la recuperación del RAG y recomienda parámetros.",
    )
    parser.add_argument("--dataset", type=Path, required=True, help="JSON con las preguntas")
    parser.add_argument(
        "--documents", type=Path, help="carpeta con los documentos (por defecto, los de la app)"
    )
    parser.add_argument("--chunk-sizes", type=_int_list, help="p. ej. 1000,2000,3000")
    parser.add_argument("--overlaps", type=_int_list, help="p. ej. 200,400")
    parser.add_argument("--top-k", type=_int_list, default=[3, 5, 8])
    parser.add_argument("--thresholds", type=_thresholds, default=_thresholds("0:0.9:0.05"))
    parser.add_argument(
        "--condensar",
        action="store_true",
        help="reformula con el modelo las preguntas que tienen historial",
    )
    parser.add_argument("--output", type=Path, help="guarda el informe completo en JSON")
    parser.add_argument(
        "--sin-preprocesado",
        action="store_true",
        help="no aplica el preprocesado del texto ni resta peso a los índices (para comparar)",
    )
    parser.add_argument(
        "--sin-progreso",
        action="store_true",
        help="no muestra el avance (por defecto se muestra en la salida de errores)",
    )
    return parser


async def main_async(
    argv: Sequence[str], settings: Settings, out: TextIO, status: TextIO | None = None
) -> int:
    """Ejecuta la evaluación: el informe va a `out` y el progreso, a `status`."""
    args = build_parser().parse_args(argv)
    cases = load_dataset(args.dataset)
    documents = (
        documents_from_folder(args.documents)
        if args.documents
        else await documents_from_app(settings)
    )
    config = await load_runtime_config(settings)

    def progress(message: str) -> None:
        if status is not None and not args.sin_progreso:
            print(f"{time.strftime('%H:%M:%S')} {message}", file=status, flush=True)

    progress(f"{len(cases)} preguntas · {len(documents)} documentos")
    sizes = args.chunk_sizes or [config.indexing.chunk_size_chars]
    overlaps = args.overlaps if args.overlaps is not None else [config.indexing.chunk_overlap_chars]
    configs = [ChunkConfig(s, min(o, s // 2)) for s in sizes for o in overlaps]
    llm = build_llm_client(config.llm, config.tls) if args.condensar else None

    report = await run_evaluation(
        cases,
        documents,
        list(dict.fromkeys(configs)),
        args.top_k,
        args.thresholds,
        build_embeddings(config),
        llm,
        progress,
        indexing=None if args.sin_preprocesado else config.indexing,
        toc_score_factor=1.0 if args.sin_preprocesado else config.rag.toc_score_factor,
    )
    out.write(format_report(report))
    if args.output:
        args.output.write_text(
            json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


def main(argv: Sequence[str] | None = None) -> int:  # pragma: no cover - envoltorio de CLI
    # La consola de Windows usa cp1252 por defecto; el informe lleva caracteres Unicode.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    return asyncio.run(main_async(argv or sys.argv[1:], get_settings(), sys.stdout, sys.stderr))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
