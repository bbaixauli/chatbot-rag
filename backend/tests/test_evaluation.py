import io
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from app.core.config import Settings
from app.core.runtime_config import RuntimeConfig
from app.db.models import Document, DocumentStatus
from app.evaluation import (
    CaseResult,
    ChunkConfig,
    EvaluationCase,
    ExpectedSource,
    RankedHit,
    _int_list,
    _thresholds,
    compute_metrics,
    documents_from_app,
    documents_from_folder,
    format_duration,
    format_report,
    load_dataset,
    main_async,
    recommend,
    run_evaluation,
)
from app.services.embeddings import FakeEmbeddings
from app.services.llm import FakeLLMClient
from app.services.vectorstore import SearchHit

BACKEND = Path(__file__).resolve().parent.parent
EXAMPLE = BACKEND / "eval" / "ejemplo"


def _hit(filename: str = "a.pdf", page: int | None = None, heading: str | None = None) -> SearchHit:
    return SearchHit("d", filename, 0, "t", page, heading, 0.5)


def test_expected_source_matching() -> None:
    assert ExpectedSource(fichero="A.pdf").matches(_hit("a.pdf"))
    assert not ExpectedSource(fichero="b.pdf").matches(_hit("a.pdf"))
    assert ExpectedSource(fichero="a.pdf", pagina=2).matches(_hit(page=2))
    assert not ExpectedSource(fichero="a.pdf", pagina=2).matches(_hit(page=3))
    assert ExpectedSource(fichero="a.pdf", seccion="solicitud").matches(
        _hit(heading="Vacaciones > Solicitud")
    )
    assert not ExpectedSource(fichero="a.pdf", seccion="Dietas").matches(_hit())


def test_load_dataset_validates(tmp_path: Path) -> None:
    cases = load_dataset(EXAMPLE / "preguntas.json")
    assert len(cases) == 9
    assert cases[2].historial == ["¿Cuántos días de vacaciones tengo al año?"]

    bad = tmp_path / "malo.json"
    for content in ['{"preguntas": []}', '{"preguntas": [{"pregunta": "x", "otra": 1}]}']:
        bad.write_text(content, encoding="utf-8")
        with pytest.raises(ValidationError):
            load_dataset(bad)


def _case(answerable: bool, *hits: tuple[float, bool]) -> CaseResult:
    return CaseResult("q", "q", answerable, [RankedHit(s, r) for s, r in hits])


RESULTS = [
    _case(True, (0.9, True), (0.5, False)),
    _case(True, (0.6, False), (0.4, True)),
    _case(True, (0.2, False)),
    _case(False, (0.35, False)),
]


def test_compute_metrics() -> None:
    m = compute_metrics(RESULTS, top_k=2, threshold=0.3)

    assert m.hit_rate == pytest.approx(2 / 3)
    assert m.mrr == pytest.approx((1 + 0.5 + 0) / 3)
    assert m.recall == pytest.approx(2 / 3)
    assert m.precision == pytest.approx((0.5 + 0.5) / 2)
    assert m.false_positive_rate == 1.0
    assert m.utility == 0.0

    strict = compute_metrics(RESULTS, top_k=1, threshold=0.5)
    assert strict.recall == pytest.approx(1 / 3)
    assert strict.false_positive_rate == 0.0
    assert strict.utility == pytest.approx(1 / 3)


def test_utility_uses_precision_without_unanswerable_questions() -> None:
    m = compute_metrics(RESULTS[:2], top_k=2, threshold=0.45)
    assert m.recall == 0.5
    assert m.precision == pytest.approx((0.5 + 0) / 2)
    assert m.utility == pytest.approx(0.5 * 0.25)
    assert compute_metrics([], 3, 0.5).utility == 0.0


def test_recommend_prefers_higher_threshold_on_ties() -> None:
    results = [_case(True, (0.8, True)), _case(False, (0.3, False))]
    best = recommend(results, top_k=1, thresholds=[0.0, 0.4, 0.5, 0.9])
    assert best.threshold == 0.5
    assert best.utility == 1.0


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0.4, "0 s"), (45, "45 s"), (185, "3 min 05 s"), (3725, "1 h 02 min")],
)
def test_format_duration(seconds: float, text: str) -> None:
    assert format_duration(seconds) == text


def test_argument_parsers() -> None:
    assert _int_list("1000, 2000,") == [1000, 2000]
    assert _thresholds("0.2,0.4") == [0.2, 0.4]
    assert _thresholds("0:0.2:0.05") == [0.0, 0.05, 0.1, 0.15, 0.2]


def test_documents_from_folder(tmp_path: Path) -> None:
    (tmp_path / "b.md").write_text("x", encoding="utf-8")
    (tmp_path / "a.PDF").write_bytes(b"x")
    (tmp_path / "notas.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub.md").mkdir()

    assert [d.filename for d in documents_from_folder(tmp_path)] == ["a.PDF", "b.md"]


async def test_run_evaluation_on_example() -> None:
    cases = load_dataset(EXAMPLE / "preguntas.json")
    documents = documents_from_folder(EXAMPLE / "documentos")

    report = await run_evaluation(
        cases,
        documents,
        [ChunkConfig(300, 0), ChunkConfig(3000, 400)],
        top_ks=[1, 3],
        thresholds=[0.0, 0.2, 0.4, 0.6],
        embeddings=FakeEmbeddings(),
        llm=FakeLLMClient(0),
    )

    assert report.questions == 9
    assert report.unanswerable == 2
    assert len(report.configs) == 2
    best = report.best
    assert best.best.hit_rate == 1.0
    assert best.best.false_positive_rate == 0.0
    assert best.best.threshold == 0.4
    follow_up = next(c for c in best.cases if c.question == "¿Y dónde se solicitan?")
    assert follow_up.query == "¿Cuántos días de vacaciones tengo al año? ¿Y dónde se solicitan?"

    text = format_report(report)
    assert "Recuperación · puntuación mínima: 0.40" in text
    assert "| Umbral | Recall |" in text


async def test_run_evaluation_reports_skipped_and_failed(tmp_path: Path) -> None:
    (tmp_path / "roto.pdf").write_bytes(b"no es un pdf")
    (tmp_path / "otro.md").write_text("# Gatos\n\nLos gatos duermen mucho.", encoding="utf-8")
    cases = [
        EvaluationCase(pregunta="¿Cuánto duermen los gatos?", esperado=[{"fichero": "otro.md"}]),
        EvaluationCase(pregunta="vacaciones", esperado=[{"fichero": "roto.pdf"}]),
    ]

    messages: list[str] = []

    report = await run_evaluation(
        cases,
        documents_from_folder(tmp_path),
        [ChunkConfig(500, 0)],
        [3],
        [0.1],
        FakeEmbeddings(),
        progress=messages.append,
    )

    assert "No se pudo leer" in report.skipped_documents["roto.pdf"]
    assert messages[0] == "Calculando los embeddings de 2 preguntas con fake-hashing…"
    assert messages[1] == "[1/1] fragmento 500, solape 0: indexando 2 documentos…"
    assert messages[2].startswith("[1/1] fragmento 500, solape 0: documento 1/2 (1 fragmentos")
    assert "quedan unos" in messages[2]
    assert messages[3] == "[1/1] fragmento 500, solape 0: documento 2/2: omitido roto.pdf"
    assert messages[4].startswith("[1/1] fragmento 500, solape 0: 1 fragmentos indexados en")
    assert messages[-1].startswith("Evaluación terminada en")
    text = format_report(report)
    assert "⚠️ Omitido `roto.pdf`" in text
    assert "### Preguntas sin la fuente esperada" in text
    assert "- vacaciones" in text

    with pytest.raises(ValueError, match="No hay documentos"):
        await run_evaluation(cases, [], [ChunkConfig(500, 0)], [3], [0.1], FakeEmbeddings())


async def test_documents_from_app(app: FastAPI, settings: Settings) -> None:
    class Reader:
        def __init__(self, data: bytes) -> None:
            self.data = data

        async def read(self, size: int = -1) -> bytes:
            chunk, self.data = self.data[:size], self.data[size:]
            return chunk

    ready = await app.state.documents.upload("guia.md", Reader(b"# Hola\n\ntexto"), "admin")
    gone = await app.state.documents.upload("borrado.md", Reader(b"# Otro\n\nmas"), "admin")
    await app.state.tasks.drain()
    app.state.documents.file_path(gone.document).unlink()
    async with app.state.sessionmaker() as session:
        session.add(
            Document(
                filename="pendiente.md",
                title="pendiente.md",
                kind="markdown",
                size_bytes=1,
                sha256="f" * 64,
                status=DocumentStatus.PENDING.value,
                uploaded_by="admin",
            )
        )
        await session.commit()

    documents = await documents_from_app(settings)

    assert [d.filename for d in documents] == ["guia.md"]
    assert documents[0].path == app.state.documents.file_path(ready.document)


async def test_cli_writes_report_and_json(tmp_path: Path) -> None:
    settings = Settings(embedding_provider="fake", llm_provider="fake", chunk_size_chars=800)
    output = tmp_path / "informe.json"
    out = io.StringIO()
    status = io.StringIO()

    code = await main_async(
        [
            "--dataset",
            str(EXAMPLE / "preguntas.json"),
            "--documents",
            str(EXAMPLE / "documentos"),
            "--top-k",
            "3",
            "--thresholds",
            "0.3,0.4",
            "--condensar",
            "--output",
            str(output),
        ],
        settings,
        out,
        status,
    )

    assert code == 0
    assert out.getvalue().startswith("# Evaluación de la recuperación")
    progress = status.getvalue()
    assert "9 preguntas · " in progress
    assert "Reformulando con el modelo 1 preguntas con historial…" in progress
    assert "Evaluación terminada en" in progress
    assert "Evaluación terminada" not in out.getvalue()
    data = json.loads(output.read_text(encoding="utf-8"))
    assert data["embedding_model"] == "fake-hashing"
    assert [(c["chunk_size"], c["chunk_overlap"]) for c in data["configs"]] == [(800, 400)]


async def test_cli_without_progress(tmp_path: Path) -> None:
    settings = Settings(embedding_provider="fake", llm_provider="fake")
    status = io.StringIO()

    code = await main_async(
        [
            "--dataset",
            str(EXAMPLE / "preguntas.json"),
            "--documents",
            str(EXAMPLE / "documentos"),
            "--sin-progreso",
        ],
        settings,
        io.StringIO(),
        status,
    )

    assert code == 0
    assert status.getvalue() == ""


async def test_run_evaluation_with_preprocessing(tmp_path: Path) -> None:
    (tmp_path / "guia.md").write_text("# Guía\n\nContenido de la guía.", encoding="utf-8")
    cases = [EvaluationCase(pregunta="contenido", esperado=[{"fichero": "guia.md"}])]
    indexing = RuntimeConfig.from_settings(Settings()).indexing

    report = await run_evaluation(
        cases,
        documents_from_folder(tmp_path),
        [ChunkConfig(500, 0)],
        [3],
        [0.1],
        FakeEmbeddings(),
        indexing=indexing,
        toc_score_factor=0.8,
    )

    assert report.preprocessing == "v1;lineas=0.5;relleno;contexto"
    assert report.toc_score_factor == 0.8
    assert (
        "Preprocesado: `v1;lineas=0.5;relleno;contexto` · peso de los fragmentos de índice: 0.8"
        in (format_report(report))
    )
    assert report.best.best.hit_rate == 1.0


async def test_cli_without_preprocessing(tmp_path: Path) -> None:
    settings = Settings(embedding_provider="fake", llm_provider="fake")
    output = tmp_path / "informe.json"

    code = await main_async(
        [
            "--dataset",
            str(EXAMPLE / "preguntas.json"),
            "--documents",
            str(EXAMPLE / "documentos"),
            "--sin-preprocesado",
            "--output",
            str(output),
        ],
        settings,
        io.StringIO(),
    )

    assert code == 0
    data = json.loads(output.read_text(encoding="utf-8"))
    assert (data["preprocessing"], data["toc_score_factor"]) == ("ninguno", 1.0)
