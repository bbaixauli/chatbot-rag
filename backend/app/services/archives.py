"""Lectura de los ZIP con varios documentos (#77).

Cada fichero admitido del ZIP se da de alta como un documento. El ZIP no se conserva y no
tiene límite de tamaño propio (el de la petición lo pone el proxy, `CHATBOT_MAX_BODY_SIZE`);
el límite de subida se aplica a cada fichero mientras se descomprime, así que lo que se
escribe en disco queda acotado aunque el ZIP sea malicioso («bomba ZIP»).
"""

import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import IO

# Ficheros que añaden los sistemas operativos y no son documentos: se omiten sin avisar.
_SYSTEM_DIRECTORIES = {"__MACOSX"}
_SYSTEM_FILES = {"thumbs.db", "desktop.ini", ".ds_store"}
# Bit 11 de las opciones generales: el nombre está en UTF-8.
_UTF8_FLAG = 0x800
_ENCRYPTED_FLAG = 0x1


class ArchiveError(Exception):
    """El ZIP no se puede leer."""


@dataclass(frozen=True, slots=True)
class ArchiveMember:
    """Fichero del ZIP: se puede leer (`info`) o se explica por qué no (`error`)."""

    # Ruta dentro del ZIP, con los acentos bien decodificados.
    name: str
    info: zipfile.ZipInfo | None = None
    error: str | None = None


def member_name(info: zipfile.ZipInfo) -> str:
    """Nombre del fichero dentro del ZIP.

    Sin la marca de UTF-8, `zipfile` lo decodifica como CP437. Los ZIP creados en Windows en
    español usan CP850 (los acentos en minúscula coinciden, las mayúsculas no) y algunas
    herramientas escriben UTF-8 sin marcarlo: se prueba UTF-8 y, si no, CP850.
    """
    if info.flag_bits & _UTF8_FLAG:
        return info.filename
    raw = info.filename.encode("cp437")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp850")


def _is_system(path: PurePosixPath) -> bool:
    parts = path.parts
    return (
        any(part in _SYSTEM_DIRECTORIES or part.startswith(".") for part in parts)
        or path.name.lower() in _SYSTEM_FILES
    )


def open_archive(path: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ArchiveError("El ZIP está dañado o no es un fichero ZIP") from exc


def members(archive: zipfile.ZipFile) -> Iterator[ArchiveMember]:
    """Ficheros del ZIP en orden, sin directorios ni ficheros de sistema."""
    for info in archive.infolist():
        if info.is_dir():
            continue
        name = member_name(info).replace("\\", "/")
        path = PurePosixPath(name)
        if _is_system(path):
            continue
        if info.flag_bits & _ENCRYPTED_FLAG:
            yield ArchiveMember(name, error="Está protegido con contraseña dentro del ZIP")
        elif path.suffix.lower() == ".zip":
            yield ArchiveMember(name, error="No se admiten ZIP dentro de otro ZIP")
        else:
            yield ArchiveMember(name, info=info)


class MemberReader:
    """Lector asíncrono de un fichero del ZIP (para `DocumentService.upload`).

    La descompresión es síncrona y rápida por trozos; los trozos los pide `upload`, que
    corta en cuanto se supera el tamaño máximo.
    """

    def __init__(self, stream: IO[bytes]) -> None:
        self._stream = stream

    async def read(self, size: int = -1) -> bytes:
        try:
            return self._stream.read(size)
        except (zipfile.BadZipFile, zlib.error, OSError, EOFError) as exc:
            raise ArchiveError("El fichero está dañado dentro del ZIP") from exc
