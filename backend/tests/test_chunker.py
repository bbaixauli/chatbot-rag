import itertools

from app.services.ingestion.chunker import chunk_sections
from app.services.ingestion.parsers import Section


def test_small_sections_become_one_chunk_each() -> None:
    chunks = chunk_sections(
        [Section("Uno.", page=1), Section("Dos.", heading="B")], size=200, overlap=20
    )

    assert [(c.index, c.text, c.page, c.heading) for c in chunks] == [
        (0, "Uno.", 1, None),
        (1, "Dos.", None, "B"),
    ]


def test_long_section_is_split_with_limit_and_overlap() -> None:
    paragraphs = [f"Párrafo número {i} con algo de texto de relleno." for i in range(40)]
    chunks = chunk_sections([Section("\n\n".join(paragraphs), page=7)], size=300, overlap=60)

    assert len(chunks) > 5
    assert all(len(c.text) <= 300 for c in chunks)
    assert all(c.page == 7 for c in chunks)
    assert [c.index for c in chunks] == list(range(len(chunks)))
    # Cada fragmento empieza con el final del anterior.
    for previous, current in itertools.pairwise(chunks):
        assert current.text.split("\n\n")[0] in previous.text
    joined = " ".join(c.text for c in chunks)
    assert all(p in joined for p in paragraphs)


def test_long_paragraph_is_split_by_sentences_and_hard_limit() -> None:
    text = "Frase corta. " * 30 + "x" * 500
    chunks = chunk_sections([Section(text)], size=200, overlap=0)

    assert all(len(c.text) <= 200 for c in chunks)
    assert "".join(c.text for c in chunks).count("x") == 500


def test_without_overlap_chunks_do_not_repeat() -> None:
    paragraphs = [f"p{i} " + "a" * 50 for i in range(10)]
    chunks = chunk_sections([Section("\n\n".join(paragraphs))], size=120, overlap=0)

    assert sum(c.text.count("p") for c in chunks) == 10


def test_empty_paragraphs_are_ignored() -> None:
    assert chunk_sections([Section("\n\n   \n\n")], size=100, overlap=10) == []
