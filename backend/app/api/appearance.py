"""Apariencia: hoja de estilos y datos públicos, y su administración (#36)."""

import hashlib
import logging
from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response, UploadFile, status
from pydantic import BaseModel

from app.api.deps import DbSession
from app.core.auth import AdminDep
from app.services.appearance import (
    LOGO_MAX_BYTES,
    MONO_FONTS,
    SANS_FONTS,
    AppearanceConfig,
    AppearanceError,
    AppearanceRepository,
    contrast_warnings,
    stylesheet,
    validate,
    validate_logo,
)

logger = logging.getLogger(__name__)

public_router = APIRouter(tags=["appearance"])
router = APIRouter(prefix="/admin/appearance", tags=["appearance"])

LOGO_PATH = "/api/v1/appearance/logo"
# El logo se muestra con <img>; además, si se abre directamente, un SVG no puede ejecutar
# nada ni cargar recursos.
LOGO_CSP = "default-src 'none'; style-src 'unsafe-inline'; sandbox"


class FooterModel(BaseModel):
    unit: str = ""
    accessibility_url: str = ""
    support_text: str = ""
    support_url: str = ""


class PublicAppearance(BaseModel):
    """Lo que necesita cualquier pantalla (también la de inicio de sesión)."""

    app_name: str
    organization: str
    header_style: Literal["clara", "marca"]
    logo_url: str | None
    logo_alt: str
    footer: FooterModel
    login_notice: str
    ai_notice: str


class AppearanceIn(BaseModel):
    app_name: str = ""
    organization: str = ""
    header_style: Literal["clara", "marca"] = "clara"
    colors: dict[str, str] = {}
    font_sans: str = "sistema"
    font_mono: str = "sistema"
    logo_alt: str = ""
    footer: FooterModel = FooterModel()
    login_notice: str = ""
    ai_notice: str = ""


class ContrastWarningOut(BaseModel):
    foreground: str
    background: str
    ratio: float
    required: float
    usage: str


class AppearanceOut(AppearanceIn):
    logo_url: str | None
    warnings: list[ContrastWarningOut]
    # Opciones de tipo de letra disponibles (clave → pila de fuentes).
    sans_fonts: dict[str, str]
    mono_fonts: dict[str, str]


async def _logo_url(repo: AppearanceRepository) -> str | None:
    logo = await repo.get_logo()
    return f"{LOGO_PATH}?v={logo.version}" if logo else None


async def _admin_out(repo: AppearanceRepository, config: AppearanceConfig) -> AppearanceOut:
    data: dict[str, Any] = asdict(config)
    return AppearanceOut(
        **data,
        logo_url=await _logo_url(repo),
        warnings=[ContrastWarningOut(**asdict(w)) for w in contrast_warnings(config)],
        sans_fonts=SANS_FONTS,
        mono_fonts=MONO_FONTS,
    )


# --- Público ---


@public_router.get("/appearance", response_model=PublicAppearance)
async def get_public_appearance(db: DbSession) -> PublicAppearance:
    repo = AppearanceRepository(db)
    config = await repo.get()
    return PublicAppearance(
        app_name=config.app_name,
        organization=config.organization,
        header_style=config.header_style,
        logo_url=await _logo_url(repo),
        logo_alt=config.logo_alt,
        footer=FooterModel(**asdict(config.footer)),
        login_notice=config.login_notice,
        ai_notice=config.ai_notice,
    )


@public_router.get("/appearance.css", response_class=Response)
async def get_stylesheet(request: Request, db: DbSession) -> Response:
    """Variables CSS de la apariencia; el navegador la revalida en cada carga (ETag)."""
    css = stylesheet(await AppearanceRepository(db).get())
    etag = f'"{hashlib.sha256(css.encode()).hexdigest()[:16]}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(css, media_type="text/css", headers=headers)


@public_router.get("/appearance/logo", response_class=Response)
async def get_logo(db: DbSession) -> Response:
    logo = await AppearanceRepository(db).get_logo()
    if logo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No hay logo configurado")
    return Response(
        logo.data,
        media_type=logo.content_type,
        headers={
            # La URL lleva la versión (?v=): se puede guardar en caché mucho tiempo.
            "Cache-Control": "public, max-age=31536000, immutable",
            "Content-Security-Policy": LOGO_CSP,
            "X-Content-Type-Options": "nosniff",
        },
    )


# --- Administración ---


@router.get("", response_model=AppearanceOut)
async def get_appearance(_: AdminDep, db: DbSession) -> AppearanceOut:
    repo = AppearanceRepository(db)
    return await _admin_out(repo, await repo.get())


@router.put("", response_model=AppearanceOut)
async def update_appearance(body: AppearanceIn, admin: AdminDep, db: DbSession) -> AppearanceOut:
    """Guarda la apariencia. Los contrastes insuficientes no lo impiden: se devuelven en
    `warnings` para que la administración los vea."""
    try:
        config = validate(body.model_dump())
    except AppearanceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    repo = AppearanceRepository(db)
    await repo.save(config, admin.username)
    await db.commit()
    warnings = contrast_warnings(config)
    logger.info(
        "Apariencia actualizada",
        extra={"user_id": admin.username, "contrast_warnings": len(warnings)},
    )
    return await _admin_out(repo, config)


@router.delete("", response_model=AppearanceOut)
async def reset_appearance(admin: AdminDep, db: DbSession) -> AppearanceOut:
    """Vuelve a los valores por defecto (el logo se conserva)."""
    repo = AppearanceRepository(db)
    config = await repo.reset(admin.username)
    await db.commit()
    return await _admin_out(repo, config)


@router.put("/logo", response_model=AppearanceOut)
async def upload_logo(file: UploadFile, admin: AdminDep, db: DbSession) -> AppearanceOut:
    data = await file.read(LOGO_MAX_BYTES + 1)
    await file.close()
    try:
        logo = validate_logo(file.content_type or "", data)
    except AppearanceError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    repo = AppearanceRepository(db)
    await repo.save_logo(logo, admin.username)
    await db.commit()
    return await _admin_out(repo, await repo.get())


@router.delete("/logo", response_model=AppearanceOut)
async def delete_logo(_: AdminDep, db: DbSession) -> AppearanceOut:
    repo = AppearanceRepository(db)
    await repo.delete_logo()
    await db.commit()
    return await _admin_out(repo, await repo.get())
