"""Categorías de los documentos (#75): un solo nivel, opcionales.

La categoría de un documento va en el texto de sus vectores («Categoría: …»): renombrar o
borrar una categoría deja desactualizados sus documentos hasta que se reindexen.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Category, Document

MAX_CATEGORY_LENGTH = 100


class CategoryError(Exception):
    """Nombre vacío o repetido."""


@dataclass(frozen=True, slots=True)
class CategoryInfo:
    id: int
    name: str
    documents: int


def clean_name(name: str) -> str:
    """Nombre sin espacios de más; error si queda vacío."""
    cleaned = " ".join(name.split())
    if not cleaned:
        raise CategoryError("El nombre de la categoría no puede estar vacío")
    return cleaned[:MAX_CATEGORY_LENGTH]


async def list_categories(session: AsyncSession) -> list[CategoryInfo]:
    """Categorías por nombre, con cuántos documentos tiene cada una."""
    rows = await session.execute(
        select(Category.id, Category.name, func.count(Document.id))
        .outerjoin(Document, Document.category_id == Category.id)
        .group_by(Category.id, Category.name)
    )
    categories = [CategoryInfo(id, name, int(count)) for id, name, count in rows.all()]
    return sorted(categories, key=lambda c: c.name.casefold())


async def _check_unique(session: AsyncSession, name: str, exclude: int | None = None) -> None:
    """Sin distinguir mayúsculas (también las acentuadas: `lower()` de SQLite solo cambia
    las ASCII, así que se compara aquí; son pocas categorías)."""
    rows = await session.execute(select(Category.id, Category.name))
    for category_id, existing in rows.all():
        if category_id != exclude and existing.casefold() == name.casefold():
            raise CategoryError(f"Ya existe la categoría «{existing}»")


async def create_category(session: AsyncSession, name: str) -> Category:
    name = clean_name(name)
    await _check_unique(session, name)
    category = Category(name=name)
    session.add(category)
    await session.flush()
    return category


async def rename_category(session: AsyncSession, category: Category, name: str) -> Category:
    name = clean_name(name)
    await _check_unique(session, name, exclude=category.id)
    category.name = name
    await session.flush()
    return category
