"""Endpoints de administración de las categorías de los documentos (#75)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, StringConstraints

from app.api.deps import DbSession
from app.core.auth import AdminDep
from app.db.models import Category
from app.services.categories import (
    MAX_CATEGORY_LENGTH,
    CategoryError,
    create_category,
    list_categories,
    rename_category,
)

router = APIRouter(prefix="/admin/categories", tags=["documents"])


class CategoryOut(BaseModel):
    id: int
    name: str
    # Documentos de la categoría.
    documents: int = 0


class CategoryIn(BaseModel):
    name: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CATEGORY_LENGTH)
    ]


async def _get_or_404(db: DbSession, category_id: int) -> Category:
    category = await db.get(Category, category_id)
    if category is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Categoría no encontrada")
    return category


def _conflict(exc: CategoryError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, str(exc))


@router.get("", response_model=list[CategoryOut])
async def get_categories(_: AdminDep, db: DbSession) -> list[CategoryOut]:
    return [
        CategoryOut(id=c.id, name=c.name, documents=c.documents) for c in await list_categories(db)
    ]


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def add_category(body: CategoryIn, _: AdminDep, db: DbSession) -> CategoryOut:
    try:
        category = await create_category(db, body.name)
    except CategoryError as exc:
        raise _conflict(exc) from exc
    await db.commit()
    return CategoryOut(id=category.id, name=category.name)


@router.patch("/{category_id}", response_model=CategoryOut)
async def update_category(
    category_id: int, body: CategoryIn, _: AdminDep, db: DbSession
) -> CategoryOut:
    """Renombra la categoría: sus documentos quedan desactualizados hasta reindexarlos."""
    category = await _get_or_404(db, category_id)
    try:
        await rename_category(db, category, body.name)
    except CategoryError as exc:
        raise _conflict(exc) from exc
    await db.commit()
    info = next(c for c in await list_categories(db) if c.id == category_id)
    return CategoryOut(id=info.id, name=info.name, documents=info.documents)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_category(category_id: int, _: AdminDep, db: DbSession) -> None:
    """Borra la categoría; sus documentos quedan sin categoría (no se borran)."""
    await db.delete(await _get_or_404(db, category_id))
    await db.commit()
