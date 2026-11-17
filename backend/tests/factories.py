"""Generadores de ficheros de ejemplo para los tests."""

from pathlib import Path

import pymupdf


def make_pdf(path: Path, pages: list[str]) -> Path:
    """Crea un PDF con una página por texto (una página vacía si el texto es "")."""
    pdf = pymupdf.open()  # type: ignore[no-untyped-call]
    for text in pages:
        page = pdf.new_page()
        if text:
            page.insert_text((72, 72), text)
    pdf.save(path)  # type: ignore[no-untyped-call]
    pdf.close()  # type: ignore[no-untyped-call]
    return path


def pdf_bytes(tmp_path: Path, pages: list[str]) -> bytes:
    return make_pdf(tmp_path / "tmp.pdf", pages).read_bytes()


MARKDOWN = """Introducción sin encabezado.

# Guía de vacaciones

Los empleados tienen 23 días laborables de vacaciones al año.

## Solicitud

Las vacaciones se solicitan en el portal del empleado con 15 días de antelación.

# Teletrabajo

Se permite teletrabajar dos días por semana.
"""

HTML = """<!doctype html>
<html><head><title>Política de gastos</title><style>p{color:red}</style></head>
<body>
<nav><a href="/">Inicio</a> Menú de navegación</nav>
<script>alert('no indexar')</script>
<p>Texto antes de cualquier encabezado.</p>
<h1>Dietas</h1>
<p>La dieta diaria nacional es de 53,34 euros.</p>
<ul><li><p>Alojamiento incluido</p></li><li>Transporte</li></ul>
<h2>Kilometraje</h2>
<pre>0,26 € / km
  con vehículo propio</pre>
<footer>Pie de página</footer>
</body></html>
"""
