"""Apariencia configurable (#36): validación, contraste, hoja de estilos, logo y API."""

import re
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from pydantic import SecretStr

from app.api import admin
from app.core.config import Settings
from app.services.appearance import (
    DEFAULT_COLORS,
    LOGO_MAX_BYTES,
    SANS_FONTS,
    AppearanceConfig,
    AppearanceError,
    contrast_ratio,
    contrast_warnings,
    stylesheet,
    validate,
    validate_logo,
)

ADMIN = {"username": "admin", "password": "secreto"}
INDEX_CSS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "index.css"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 16
SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    b'<rect width="10" height="10"/></svg>'
)

# Valores del Ayuntamiento de Valladolid (manual de estilos, 3.1).
VALLADOLID = {
    "marca": "#980046",
    "marca-oscuro": "#4E1036",
    "sobre-marca": "#FFFFFF",
    "marca-suave": "#FBEFF4",
    "enlace": "#7A0038",
    "texto": "#1F1A1C",
    "texto-secundario": "#5C5458",
    "borde-control": "#8A8286",
    "fondo": "#FFFFFF",
    "fondo-alt": "#F6F3F4",
    "foco": "#005FCC",
    "error": "#B3261E",
    "exito": "#1E6B3A",
    "aviso": "#8A5A00",
    "info": "#0B5C8A",
}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    settings.admin_password = SecretStr(ADMIN["password"])
    return settings


@pytest.fixture(autouse=True)
def no_login_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(admin, "FAILED_LOGIN_DELAY_SECONDS", 0)


@pytest.fixture
async def admin_client(client: AsyncClient) -> AsyncClient:
    assert (await client.post("/api/v1/admin/session", json=ADMIN)).status_code == 200
    return client


# --- Reglas ---


def test_defaults_match_the_frontend_tokens() -> None:
    """El tema por defecto del servidor y el de index.css deben ser el mismo."""
    css = INDEX_CSS.read_text(encoding="utf-8")
    tokens = dict(re.findall(r"--color-([a-z-]+):\s*(#[0-9a-fA-F]{6})", css))
    assert tokens == DEFAULT_COLORS
    sans = re.search(r"--font-sans:\s*([^;]+);", css)
    assert sans is not None
    assert sans.group(1).strip() == SANS_FONTS["sistema"]


@pytest.mark.parametrize(
    ("foreground", "background", "expected"),
    [
        ("#000000", "#ffffff", 21.0),
        ("#ffffff", "#ffffff", 1.0),
        ("#2563eb", "#ffffff", 5.17),
        ("#980046", "#ffffff", 8.67),  # valores del manual
        ("#5c5458", "#ffffff", 7.33),
    ],
)
def test_contrast_ratio(foreground: str, background: str, expected: float) -> None:
    assert round(contrast_ratio(foreground, background), 2) == expected


def test_default_and_valladolid_themes_have_no_contrast_warnings() -> None:
    assert contrast_warnings(AppearanceConfig()) == []
    valladolid = validate({"colors": VALLADOLID, "header_style": "marca", "font_sans": "trebuchet"})
    assert contrast_warnings(valladolid) == []


def test_insufficient_contrast_is_reported() -> None:
    config = validate({"colors": {"texto-secundario": "#9ca3af", "borde-control": "#d1d5db"}})
    warnings = {(w.foreground, w.background): w for w in contrast_warnings(config)}
    assert warnings[("texto-secundario", "fondo")].required == 4.5
    assert warnings[("texto-secundario", "fondo")].ratio < 4.5
    assert warnings[("borde-control", "fondo")].required == 3.0
    # La cabecera clara con texto oscuro sigue bien.
    assert ("cabecera (texto)", "cabecera (fondo)") not in warnings


def test_header_contrast_depends_on_its_style() -> None:
    light_brand = {"marca": "#f08db8"}  # rosa claro: no sirve como fondo de texto blanco
    assert not any(
        w.foreground == "cabecera (texto)"
        for w in contrast_warnings(validate({"colors": light_brand}))
    )
    assert any(
        w.foreground == "cabecera (texto)"
        for w in contrast_warnings(validate({"colors": light_brand, "header_style": "marca"}))
    )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ({"colors": {"marca": "red"}}, "hexadecimal"),
        ({"colors": {"marca": "#12345"}}, "hexadecimal"),
        ({"colors": {"marca": "#123456; } body { display: none"}}, "hexadecimal"),
        ({"colors": {"inventado": "#123456"}}, "desconocido"),
        ({"font_sans": "Comic Sans"}, "no admitido"),
        ({"font_mono": "x"}, "no admitido"),
        ({"header_style": "oscura"}, "cabecera"),
        ({"footer": {"accessibility_url": "javascript:alert(1)"}}, "http"),
        ({"footer": {"support_url": "ftp://x"}}, "http"),
        ({"footer": {"support_url": "https://x.org/" + "a" * 500}}, "500"),
        ({"app_name": "x" * 81}, "máximo"),
    ],
)
def test_invalid_values_are_rejected(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(AppearanceError, match=message):
        validate(raw)


def test_validate_normalizes_texts_and_defaults() -> None:
    config = validate({"app_name": "  ", "organization": " Ayuntamiento   de X ", "colors": {}})
    assert config.app_name == "Chatbot RAG"
    assert config.organization == "Ayuntamiento de X"
    assert config.colors == DEFAULT_COLORS


def test_stylesheet() -> None:
    css = stylesheet(validate({"colors": {"marca": "#980046"}, "header_style": "marca"}))
    assert "--color-marca: #980046;" in css
    assert "--color-cabecera: var(--color-marca);" in css
    assert "--color-sobre-cabecera: var(--color-sobre-marca);" in css
    assert f"--font-sans: {SANS_FONTS['sistema']};" in css
    light = stylesheet(AppearanceConfig())
    assert "--color-cabecera: var(--color-fondo);" in light


@pytest.mark.parametrize(
    ("data", "content_type", "expected"),
    [
        (PNG, "image/png", "image/png"),
        (WEBP, "image/webp", "image/webp"),
        (SVG, "image/svg+xml", "image/svg+xml"),
        (b'<?xml version="1.0"?>\n' + SVG, "", "image/svg+xml"),
    ],
)
def test_logo_formats(data: bytes, content_type: str, expected: str) -> None:
    assert validate_logo(content_type, data).content_type == expected


@pytest.mark.parametrize(
    ("data", "content_type", "message"),
    [
        (b"", "image/png", "vacío"),
        (b"GIF89a" + b"\x00" * 10, "image/gif", "Formato"),
        (b"<html><script>alert(1)</script></html>", "image/svg+xml", "Formato"),
        (PNG, "image/svg+xml", "no coincide"),
        (PNG + b"\x00" * LOGO_MAX_BYTES, "image/png", "512 KB"),
    ],
)
def test_invalid_logos_are_rejected(data: bytes, content_type: str, message: str) -> None:
    with pytest.raises(AppearanceError, match=message):
        validate_logo(content_type, data)


# --- API pública ---


async def test_public_appearance_defaults(client: AsyncClient) -> None:
    body = (await client.get("/api/v1/appearance")).json()
    assert body == {
        "app_name": "Chatbot RAG",
        "organization": "",
        "header_style": "clara",
        "logo_url": None,
        "logo_alt": "",
        "footer": {"unit": "", "accessibility_url": "", "support_text": "", "support_url": ""},
        "login_notice": "",
        "ai_notice": "",
    }
    assert (await client.get("/api/v1/appearance/logo")).status_code == 404


async def test_stylesheet_endpoint_is_cached_with_etag(client: AsyncClient) -> None:
    response = await client.get("/api/v1/appearance.css")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/css")
    assert response.headers["cache-control"] == "no-cache"
    assert "--color-marca: #2563eb;" in response.text
    etag = response.headers["etag"]

    again = await client.get("/api/v1/appearance.css", headers={"If-None-Match": etag})
    assert again.status_code == 304


# --- Administración ---


async def test_admin_reads_the_current_appearance(admin_client: AsyncClient) -> None:
    body = (await admin_client.get("/api/v1/admin/appearance")).json()
    assert body["colors"] == DEFAULT_COLORS
    assert body["warnings"] == []
    assert set(body["mono_fonts"]) == {"sistema", "consolas", "courier"}


async def test_only_admins_change_the_appearance(client: AsyncClient) -> None:
    assert (await client.get("/api/v1/admin/appearance")).status_code == 401
    assert (await client.put("/api/v1/admin/appearance", json={})).status_code == 401
    assert (await client.delete("/api/v1/admin/appearance")).status_code == 401
    files = {"file": ("logo.png", PNG, "image/png")}
    assert (await client.put("/api/v1/admin/appearance/logo", files=files)).status_code == 401
    assert (await client.delete("/api/v1/admin/appearance/logo")).status_code == 401


async def test_save_applies_everywhere(admin_client: AsyncClient) -> None:
    payload = {
        "app_name": "Asistente municipal",
        "organization": "Ayuntamiento de Ejemplo",
        "header_style": "marca",
        "colors": VALLADOLID,
        "font_sans": "trebuchet",
        "font_mono": "consolas",
        "footer": {
            "unit": "Servicio de Informática",
            "accessibility_url": "https://ejemplo.org/accesibilidad",
            "support_text": "Soporte 010",
            "support_url": "https://ejemplo.org/soporte",
        },
        "login_notice": "Uso exclusivo para fines municipales.",
        "ai_notice": "Respuestas generadas por IA.",
    }
    response = await admin_client.put("/api/v1/admin/appearance", json=payload)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["warnings"] == []
    assert body["colors"]["marca"] == "#980046"
    assert "trebuchet" in body["sans_fonts"]

    public = (await admin_client.get("/api/v1/appearance")).json()
    assert public["app_name"] == "Asistente municipal"
    assert public["header_style"] == "marca"
    assert public["footer"]["unit"] == "Servicio de Informática"
    css = (await admin_client.get("/api/v1/appearance.css")).text
    assert "--color-marca: #980046;" in css
    assert "Trebuchet MS" in css
    assert "Consolas" in css

    reset = await admin_client.delete("/api/v1/admin/appearance")
    assert reset.json()["colors"] == DEFAULT_COLORS
    assert (await admin_client.get("/api/v1/appearance")).json()["app_name"] == "Chatbot RAG"


async def test_low_contrast_is_saved_with_warnings(admin_client: AsyncClient) -> None:
    response = await admin_client.put(
        "/api/v1/admin/appearance", json={"colors": {"enlace": "#93c5fd"}}
    )
    assert response.status_code == 200
    [warning] = response.json()["warnings"]
    assert (warning["foreground"], warning["background"]) == ("enlace", "fondo")
    assert warning["ratio"] < warning["required"] == 4.5
    assert "--color-enlace: #93c5fd;" in (await admin_client.get("/api/v1/appearance.css")).text


async def test_invalid_values_are_not_saved(admin_client: AsyncClient) -> None:
    response = await admin_client.put(
        "/api/v1/admin/appearance", json={"colors": {"marca": "url(javascript:x)"}}
    )
    assert response.status_code == 422
    assert "hexadecimal" in response.json()["detail"]


async def test_logo_upload_serve_and_delete(admin_client: AsyncClient) -> None:
    files = {"file": ("escudo.svg", SVG, "image/svg+xml")}
    response = await admin_client.put("/api/v1/admin/appearance/logo", files=files)
    assert response.status_code == 200, response.text
    logo_url = response.json()["logo_url"]
    assert logo_url.startswith("/api/v1/appearance/logo?v=")
    assert (await admin_client.get("/api/v1/appearance")).json()["logo_url"] == logo_url

    served = await admin_client.get(logo_url)
    assert served.status_code == 200
    assert served.content == SVG
    assert served.headers["content-type"] == "image/svg+xml"
    assert "sandbox" in served.headers["content-security-policy"]
    assert served.headers["x-content-type-options"] == "nosniff"

    # Otro logo cambia la versión de la URL (caché del navegador).
    other = await admin_client.put(
        "/api/v1/admin/appearance/logo", files={"file": ("logo.png", PNG, "image/png")}
    )
    assert other.json()["logo_url"] != logo_url
    # Restablecer la apariencia conserva el logo.
    assert (await admin_client.delete("/api/v1/admin/appearance")).json()["logo_url"]

    deleted = await admin_client.delete("/api/v1/admin/appearance/logo")
    assert deleted.json()["logo_url"] is None
    assert (await admin_client.get("/api/v1/appearance/logo")).status_code == 404
    # Borrar sin logo no falla.
    assert (await admin_client.delete("/api/v1/admin/appearance/logo")).status_code == 200


async def test_invalid_logo_is_rejected(admin_client: AsyncClient) -> None:
    files = {"file": ("x.svg", b"<html><script>alert(1)</script>", "image/svg+xml")}
    response = await admin_client.put("/api/v1/admin/appearance/logo", files=files)
    assert response.status_code == 422
    assert "Formato" in response.json()["detail"]


async def test_stored_invalid_value_falls_back_to_defaults(admin_client: AsyncClient) -> None:
    """Un valor guardado que deja de ser válido no deja la aplicación sin estilos."""
    from app.db.models import AppSetting
    from app.services.appearance import APPEARANCE_KEY

    app = admin_client._transport.app  # type: ignore[attr-defined]
    async with app.state.sessionmaker() as db:
        db.add(AppSetting(key=APPEARANCE_KEY, value={"font_sans": "x"}, updated_by="test"))
        await db.commit()

    assert "--color-marca: #2563eb;" in (await admin_client.get("/api/v1/appearance.css")).text
