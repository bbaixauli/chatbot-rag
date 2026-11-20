"""Apariencia configurable de la aplicación: colores, tipos de letra, logo y textos (#36).

Los colores son los tokens del manual de estilos (frontend/src/index.css). El servidor
genera con ellos una hoja de estilos (`/api/v1/appearance.css`) que el frontend carga
antes de pintar. Solo se admiten colores hexadecimales y tipos de letra de una lista
cerrada, así que la hoja no puede contener nada que no se haya validado.

Los contrastes insuficientes (WCAG 2.1 AA) no impiden guardar: se devuelven como avisos.
"""

import base64
import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import AppSetting, utcnow

APPEARANCE_KEY = "appearance"
LOGO_KEY = "appearance_logo"

HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
LOGO_MAX_BYTES = 512 * 1024

# Tema genérico por defecto: debe coincidir con frontend/src/index.css (lo comprueba un test).
DEFAULT_COLORS: dict[str, str] = {
    "marca": "#2563eb",
    "marca-oscuro": "#1e40af",
    "sobre-marca": "#ffffff",
    "marca-suave": "#eff6ff",
    "enlace": "#1d4ed8",
    "texto": "#111827",
    "texto-secundario": "#4b5563",
    "borde-control": "#6b7280",
    "borde": "#e5e7eb",
    "fondo": "#ffffff",
    "fondo-alt": "#f9fafb",
    "foco": "#005fcc",
    "error": "#b91c1c",
    "error-suave": "#fef2f2",
    "exito": "#047857",
    "exito-suave": "#ecfdf5",
    "aviso": "#92400e",
    "aviso-suave": "#fffbeb",
    "info": "#1d4ed8",
    "info-suave": "#eff6ff",
}

# Tipos de letra instalados en los equipos (el manual pide no descargar fuentes web).
SANS_FONTS: dict[str, str] = {
    "sistema": "system-ui, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif",
    "trebuchet": "'Trebuchet MS', 'Segoe UI', Arial, sans-serif",
    "segoe": "'Segoe UI', Arial, sans-serif",
    "arial": "Arial, Helvetica, sans-serif",
    "verdana": "Verdana, Geneva, sans-serif",
    "georgia": "Georgia, 'Times New Roman', serif",
}
MONO_FONTS: dict[str, str] = {
    "sistema": "ui-monospace, Consolas, 'Courier New', monospace",
    "consolas": "Consolas, 'Courier New', monospace",
    "courier": "'Courier New', Courier, monospace",
}

HeaderStyle = Literal["clara", "marca"]
HEADER_STYLES: tuple[HeaderStyle, ...] = ("clara", "marca")
DEFAULT_APP_NAME = "Chatbot RAG"

LOGO_TYPES = {"image/png", "image/webp", "image/svg+xml"}


class AppearanceError(ValueError):
    """Valor de apariencia no válido."""


@dataclass(frozen=True, slots=True)
class FooterConfig:
    """Datos del pie (sección 4.1 del manual). Vacíos: no se muestran."""

    unit: str = ""
    accessibility_url: str = ""
    support_text: str = ""
    support_url: str = ""


@dataclass(frozen=True, slots=True)
class AppearanceConfig:
    """Apariencia de la aplicación (por defecto, el tema genérico)."""

    app_name: str = DEFAULT_APP_NAME
    organization: str = ""
    header_style: HeaderStyle = "clara"
    colors: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_COLORS))
    font_sans: str = "sistema"
    font_mono: str = "sistema"
    logo_alt: str = ""
    footer: FooterConfig = field(default_factory=FooterConfig)
    login_notice: str = ""
    ai_notice: str = ""


@dataclass(frozen=True, slots=True)
class Logo:
    content_type: str
    data: bytes

    @property
    def version(self) -> str:
        """Cambia con el contenido: invalida la caché del navegador."""
        return hashlib.sha256(self.data).hexdigest()[:12]


@dataclass(frozen=True, slots=True)
class ContrastWarning:
    """Par de colores que no alcanza el contraste mínimo de WCAG 2.1 AA."""

    foreground: str
    background: str
    ratio: float
    required: float
    usage: str


# --- Contraste (WCAG 2.1, luminancia relativa) ---


def _luminance(color: str) -> float:
    channels = [int(color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(foreground: str, background: str) -> float:
    """Relación de contraste entre dos colores hexadecimales (1 a 21)."""
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def header_colors(config: AppearanceConfig) -> tuple[str, str]:
    """Fondo y texto de la cabecera según su estilo."""
    c = config.colors
    if config.header_style == "marca":
        return c["marca"], c["sobre-marca"]
    return c["fondo"], c["texto"]


def contrast_warnings(config: AppearanceConfig) -> list[ContrastWarning]:
    """Pares de colores usados juntos que no alcanzan el mínimo (texto 4,5:1; controles 3:1)."""
    c = config.colors
    header_bg, header_fg = header_colors(config)
    pairs: list[tuple[str, str, str, str, float]] = [
        ("texto", "fondo", c["texto"], c["fondo"], 4.5),
        ("texto", "fondo-alt", c["texto"], c["fondo-alt"], 4.5),
        ("texto", "marca-suave", c["texto"], c["marca-suave"], 4.5),
        ("texto-secundario", "fondo", c["texto-secundario"], c["fondo"], 4.5),
        ("texto-secundario", "fondo-alt", c["texto-secundario"], c["fondo-alt"], 4.5),
        ("enlace", "fondo", c["enlace"], c["fondo"], 4.5),
        ("marca", "fondo", c["marca"], c["fondo"], 4.5),
        ("sobre-marca", "marca", c["sobre-marca"], c["marca"], 4.5),
        ("sobre-marca", "marca-oscuro", c["sobre-marca"], c["marca-oscuro"], 4.5),
        ("cabecera (texto)", "cabecera (fondo)", header_fg, header_bg, 4.5),
        ("borde-control", "fondo", c["borde-control"], c["fondo"], 3.0),
        ("foco", "fondo", c["foco"], c["fondo"], 3.0),
        ("fondo", "error", c["fondo"], c["error"], 4.5),
    ]
    for state in ("error", "exito", "aviso", "info"):
        pairs.append((state, "fondo", c[state], c["fondo"], 4.5))
        pairs.append((state, f"{state}-suave", c[state], c[f"{state}-suave"], 4.5))
    usages = {
        "cabecera (texto)": "Texto de la cabecera",
        "borde-control": "Bordes de campos y botones",
        "foco": "Anillo de foco",
        "sobre-marca": "Texto sobre el color de marca (botones, cabecera)",
        "marca": "Texto del botón secundario",
        "fondo": "Texto del botón de peligro",
    }
    warnings = []
    for fg_name, bg_name, fg, bg, required in pairs:
        ratio = contrast_ratio(fg, bg)
        if ratio < required:
            warnings.append(
                ContrastWarning(
                    foreground=fg_name,
                    background=bg_name,
                    ratio=round(ratio, 2),
                    required=required,
                    usage=usages.get(fg_name, "Texto"),
                )
            )
    return warnings


# --- Validación ---


def _text(value: Any, name: str, max_length: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > max_length:
        raise AppearanceError(f"{name}: máximo {max_length} caracteres")
    return text


def _url(value: Any, name: str) -> str:
    url = str(value or "").strip()
    if url and not re.match(r"^https?://[^\s<>\"']+$", url):
        raise AppearanceError(f"{name}: debe ser una dirección http:// o https://")
    if len(url) > 500:
        raise AppearanceError(f"{name}: máximo 500 caracteres")
    return url


def validate(raw: dict[str, Any]) -> AppearanceConfig:
    """Construye la configuración a partir de datos sin validar (colores, fuentes, URL…)."""
    colors = dict(DEFAULT_COLORS)
    for name, value in (raw.get("colors") or {}).items():
        if name not in DEFAULT_COLORS:
            raise AppearanceError(f"Color desconocido: {name}")
        if not isinstance(value, str) or not HEX_COLOR.match(value):
            raise AppearanceError(f"{name}: debe ser un color hexadecimal como #1A2B3C")
        colors[name] = value.lower()
    font_sans = raw.get("font_sans") or "sistema"
    font_mono = raw.get("font_mono") or "sistema"
    if font_sans not in SANS_FONTS:
        raise AppearanceError(f"Tipo de letra no admitido: {font_sans}")
    if font_mono not in MONO_FONTS:
        raise AppearanceError(f"Tipo de letra monoespaciado no admitido: {font_mono}")
    header_style = next(
        (h for h in HEADER_STYLES if h == (raw.get("header_style") or "clara")), None
    )
    if header_style is None:
        raise AppearanceError("Estilo de cabecera no admitido")
    footer = raw.get("footer") or {}
    app_name = _text(raw.get("app_name"), "Nombre de la aplicación", 80)
    return AppearanceConfig(
        app_name=app_name or DEFAULT_APP_NAME,
        organization=_text(raw.get("organization"), "Organización", 120),
        header_style=header_style,
        colors=colors,
        font_sans=font_sans,
        font_mono=font_mono,
        logo_alt=_text(raw.get("logo_alt"), "Texto alternativo del logo", 200),
        footer=FooterConfig(
            unit=_text(footer.get("unit"), "Unidad responsable", 200),
            accessibility_url=_url(footer.get("accessibility_url"), "Declaración de accesibilidad"),
            support_text=_text(footer.get("support_text"), "Soporte", 200),
            support_url=_url(footer.get("support_url"), "Enlace de soporte"),
        ),
        login_notice=_text(raw.get("login_notice"), "Aviso de acceso", 500),
        ai_notice=_text(raw.get("ai_notice"), "Aviso de inteligencia artificial", 500),
    )


def validate_logo(content_type: str, data: bytes) -> Logo:
    """Comprueba tipo (por el contenido, no solo la cabecera) y tamaño del logo."""
    if not data:
        raise AppearanceError("El fichero está vacío")
    if len(data) > LOGO_MAX_BYTES:
        raise AppearanceError("El logo supera el tamaño máximo (512 KB)")
    head = data[:512].lstrip()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        detected = "image/png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        detected = "image/webp"
    elif head.startswith((b"<svg", b"<?xml")) and b"<svg" in data[:4096]:
        detected = "image/svg+xml"
    else:
        raise AppearanceError("Formato no admitido: usa SVG, PNG o WebP")
    if content_type and content_type in LOGO_TYPES and content_type != detected:
        raise AppearanceError("El contenido del fichero no coincide con su tipo")
    return Logo(content_type=detected, data=data)


# --- Hoja de estilos ---


def stylesheet(config: AppearanceConfig) -> str:
    """Variables CSS de la apariencia (sustituyen a las de `@theme` del frontend)."""
    header_bg, header_fg = (
        ("var(--color-marca)", "var(--color-sobre-marca)")
        if config.header_style == "marca"
        else ("var(--color-fondo)", "var(--color-texto)")
    )
    lines = [f"  --color-{name}: {value};" for name, value in config.colors.items()]
    lines += [
        f"  --color-cabecera: {header_bg};",
        f"  --color-sobre-cabecera: {header_fg};",
        f"  --font-sans: {SANS_FONTS[config.font_sans]};",
        f"  --font-mono: {MONO_FONTS[config.font_mono]};",
    ]
    return (
        "/* Apariencia configurada en la administración. */\n:root {\n" + "\n".join(lines) + "\n}\n"
    )


# --- Persistencia ---


def _to_json(config: AppearanceConfig) -> dict[str, Any]:
    return asdict(config)


class AppearanceRepository:
    """Lectura y escritura de la apariencia y del logo (en `app_settings`, así entran en
    las copias de seguridad)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self) -> AppearanceConfig:
        setting = await self._session.get(AppSetting, APPEARANCE_KEY)
        if setting is None:
            return AppearanceConfig()
        try:
            return validate(setting.value)
        except AppearanceError:
            # Un valor guardado que ya no es válido no debe dejar la aplicación sin estilos.
            return AppearanceConfig()

    async def save(self, config: AppearanceConfig, author: str) -> AppearanceConfig:
        await self._put(APPEARANCE_KEY, _to_json(config), author)
        return config

    async def reset(self, author: str) -> AppearanceConfig:
        return await self.save(AppearanceConfig(), author)

    async def get_logo(self) -> Logo | None:
        setting = await self._session.get(AppSetting, LOGO_KEY)
        if setting is None:
            return None
        value = setting.value
        return Logo(content_type=value["content_type"], data=base64.b64decode(value["data"]))

    async def save_logo(self, logo: Logo, author: str) -> Logo:
        value = {"content_type": logo.content_type, "data": base64.b64encode(logo.data).decode()}
        await self._put(LOGO_KEY, value, author)
        return logo

    async def delete_logo(self) -> bool:
        setting = await self._session.get(AppSetting, LOGO_KEY)
        if setting is None:
            return False
        await self._session.delete(setting)
        await self._session.flush()
        return True

    async def _put(self, key: str, value: dict[str, Any], author: str) -> None:
        setting = await self._session.get(AppSetting, key)
        if setting is None:
            self._session.add(AppSetting(key=key, value=value, updated_by=author))
        else:
            setting.value = value
            setting.updated_by = author
            setting.updated_at = utcnow()
        await self._session.flush()
