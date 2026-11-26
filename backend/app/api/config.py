"""Configuración de la aplicación editable desde la administración (#42).

- `GET /admin/config`: todos los grupos. Los secretos nunca se devuelven: solo si están
  configurados (y si no se pueden descifrar).
- `PUT /admin/config/{grupo}`: cambia los campos enviados y se aplica de inmediato. En los
  secretos, un valor vacío o ausente conserva el actual; para quitarlo se indica en
  `clear_secrets`.
- `POST /admin/config/{grupo}/test`: prueba la conexión (modelo de lenguaje, embeddings o
  directorio LDAP) con los valores enviados, sin guardarlos.

Salvaguardas del grupo `users`: no se guarda una configuración que deje la administración
sin acceso, y para activar LDAP (o cambiar su conexión) el directorio tiene que responder.
"""

import asyncio
import logging
from datetime import datetime
from typing import Annotated, Any, get_args

from fastapi import APIRouter, Body, HTTPException, Request, status
from pydantic import BaseModel, SecretStr, ValidationError

from app.api.admin import admin_enabled
from app.api.deps import SettingsDep
from app.core.auth import AdminDep
from app.core.config import Settings
from app.core.ldap_auth import (
    ConfigurableLdap,
    DirectoryUnavailableError,
    InvalidCredentialsError,
)
from app.core.runtime_config import (
    GROUPS,
    ConfigGroup,
    EmbeddingConfig,
    LlmConfig,
    RuntimeConfig,
    UsersConfig,
)
from app.services.documents import DocumentService
from app.services.embeddings import Embeddings, build_embeddings
from app.services.llm import ChatMessage as LLMMessage
from app.services.llm import LLMError, build_llm_client
from app.services.prompt_budget import chars_to_tokens
from app.services.runtime_config import ConfigStore

router = APIRouter(prefix="/admin/config", tags=["admin"])
logger = logging.getLogger(__name__)

# Límite de una prueba de conexión (aunque el timeout configurado sea mayor).
TEST_TIMEOUT_SECONDS = 60.0
TEST_SAMPLE_CHARS = 200
FAKE_PROVIDER = "fake"

ConfigBody = Annotated[dict[str, Any], Body()]
# Grupos con prueba de conexión.
TESTABLE = ("llm", "embeddings", "users")
# Credenciales opcionales para probar el inicio de sesión de un usuario del directorio.
TEST_USER_FIELDS = ("test_username", "test_password")


class SecretOut(BaseModel):
    configured: bool
    # Guardado con otra clave (cambió CHATBOT_SESSION_SECRET): hay que volver a introducirlo.
    unreadable: bool = False


class GroupOut(BaseModel):
    values: dict[str, Any]
    secret_fields: list[str]
    reindex_fields: list[str]
    updated_by: str
    updated_at: datetime | None


class ConfigOut(BaseModel):
    groups: dict[str, GroupOut]
    # Proveedores que se pueden elegir (los simulados, solo fuera de producción).
    providers: dict[str, list[str]]


class SaveOut(BaseModel):
    group: str
    config: GroupOut
    requires_reindex: bool
    warnings: list[str]


class TestOut(BaseModel):
    ok: bool
    detail: str


def _store(request: Request) -> ConfigStore:
    store: ConfigStore = request.app.state.config
    return store


def _documents(request: Request) -> DocumentService:
    documents: DocumentService = request.app.state.documents
    return documents


def _group_class(name: str) -> type[ConfigGroup]:
    cls = GROUPS.get(name)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Grupo de configuración desconocido")
    return cls


def _providers(settings: Settings) -> dict[str, list[str]]:
    def allowed(cls: type[ConfigGroup]) -> list[str]:
        options = [str(option) for option in get_args(cls.model_fields["provider"].annotation)]
        if settings.environment == "production":
            options.remove(FAKE_PROVIDER)
        return options

    return {"llm": allowed(LlmConfig), "embeddings": allowed(EmbeddingConfig)}


def _group_out(store: ConfigStore, name: str) -> GroupOut:
    group = store.current.group(name)
    values: dict[str, Any] = group.model_dump(mode="json", exclude=set(group.SECRET_FIELDS))
    for field in group.SECRET_FIELDS:
        values[field] = SecretOut(
            configured=getattr(group, field) is not None,
            unreadable=f"{name}.{field}" in store.unreadable_secrets,
        ).model_dump()
    info = store.info(name)
    return GroupOut(
        values=values,
        secret_fields=list(group.SECRET_FIELDS),
        reindex_fields=list(group.REINDEX_FIELDS),
        updated_by=info.updated_by,
        updated_at=info.updated_at,
    )


def _error_detail(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'valor'}: {error['msg']}"
        for error in exc.errors(include_url=False)
    )


def _candidate(
    name: str, store: ConfigStore, settings: Settings, body: dict[str, Any]
) -> ConfigGroup:
    """Valores actuales del grupo con los cambios de `body` aplicados y validados."""
    cls = _group_class(name)
    current = store.current.group(name)
    values = {field: getattr(current, field) for field in cls.model_fields}
    changes = dict(body)
    clear = changes.pop("clear_secrets", None) or []
    if not isinstance(clear, list) or any(field not in cls.SECRET_FIELDS for field in clear):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "clear_secrets solo admite campos secretos"
        )
    unknown = sorted(set(changes) - set(cls.model_fields))
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Campos desconocidos: {', '.join(unknown)}"
        )
    for field, value in changes.items():
        if field in cls.SECRET_FIELDS:
            if value is None or value == "":
                continue  # se conserva el secreto actual
            if not isinstance(value, str):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT, f"{field}: debe ser un texto"
                )
            values[field] = SecretStr(value)
        else:
            values[field] = value
    for field in clear:
        values[field] = None
    try:
        candidate = cls.model_validate(values)
    except ValidationError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, _error_detail(exc)) from exc
    provider = getattr(candidate, "provider", None)
    if provider == FAKE_PROVIDER and settings.environment == "production":
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "El proveedor simulado solo está disponible fuera de producción",
        )
    return candidate


def _changed(old: ConfigGroup, new: ConfigGroup) -> list[str]:
    return [field for field in type(old).model_fields if getattr(old, field) != getattr(new, field)]


@router.get("", response_model=ConfigOut)
async def get_config(_: AdminDep, request: Request, settings: SettingsDep) -> ConfigOut:
    store = _store(request)
    return ConfigOut(
        groups={name: _group_out(store, name) for name in GROUPS},
        providers=_providers(settings),
    )


@router.put("/{group}", response_model=SaveOut)
async def update_config(
    group: str, body: ConfigBody, admin: AdminDep, request: Request, settings: SettingsDep
) -> SaveOut:
    store = _store(request)
    candidate = _candidate(group, store, settings, body)
    current = store.current.group(group)
    changed = _changed(current, candidate)
    requires_reindex = any(field in candidate.REINDEX_FIELDS for field in changed)
    warnings: list[str] = []
    if requires_reindex:
        documents = _documents(request)
        # Mezclar vectores de dos modelos (o dos troceados) en el índice lo estropea.
        busy = await documents.indexing_in_progress()
        if busy:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Hay {busy} documentos pendientes de indexar: espera a que terminen antes "
                "de cambiar el modelo de embeddings o el troceado",
            )
        if (await documents.stats()).documents:
            warnings.append(
                "Los documentos se indexaron con la configuración anterior: reindéxalos todos "
                "(Administración → Documentos → Reindexar todo)."
            )
    if isinstance(candidate, UsersConfig) and isinstance(current, UsersConfig):
        warnings += await _check_users_change(request, settings, current, candidate, changed)
    if group in ("llm", "rag"):
        warnings += _window_warnings(store.current.replace_group(group, candidate))
    if changed:
        await store.save(group, candidate, admin.username)
        logger.info(
            "Configuración «%s» actualizada: %s",
            group,
            ", ".join(changed),
            extra={"user_id": admin.username},
        )
    return SaveOut(
        group=group,
        config=_group_out(store, group),
        requires_reindex=requires_reindex,
        warnings=warnings,
    )


def _window_warnings(config: RuntimeConfig) -> list[str]:
    """Avisa si el documento completo no cabe en la ventana del modelo (#73)."""
    limit = config.llm.prompt_max_tokens
    full = config.rag.full_document_max_chars
    if limit is None or not full or chars_to_tokens(full) <= limit:
        return []
    return [
        f"«Documento completo hasta» ({full} caracteres, unos {chars_to_tokens(full)} tokens) "
        f"no cabe en la ventana del modelo ({limit} tokens para el prompt): los documentos más "
        "largos se enviarán por fragmentos. Reduce ese valor o amplía la ventana."
    ]


@router.post("/{group}/test", response_model=TestOut)
async def check_connection(
    group: str, body: ConfigBody, _: AdminDep, request: Request, settings: SettingsDep
) -> TestOut:
    """Prueba la conexión con los valores enviados (completados con los guardados).

    En `users` se puede enviar además `test_username` y `test_password` para comprobar el
    inicio de sesión de un usuario (no se guardan).
    """
    if group not in TESTABLE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Este grupo no tiene prueba de conexión")
    store = _store(request)
    values = dict(body)
    test_user = [str(values.pop(field, "") or "") for field in TEST_USER_FIELDS]
    candidate = _candidate(group, store, settings, values)
    try:
        async with asyncio.timeout(TEST_TIMEOUT_SECONDS):
            if isinstance(candidate, LlmConfig):
                return await _check_llm(candidate)
            if isinstance(candidate, UsersConfig):
                return await _check_directory(request, candidate, *test_user)
            config = store.current.replace_group(group, candidate)
            return await _check_embeddings(build_embeddings(config))
    except TimeoutError:
        return TestOut(ok=False, detail=f"Sin respuesta en {TEST_TIMEOUT_SECONDS:.0f} s")
    except Exception as exc:
        logger.info("Prueba de conexión «%s» fallida", group, exc_info=True)
        return TestOut(ok=False, detail=str(exc) or type(exc).__name__)


async def _check_llm(config: LlmConfig) -> TestOut:
    client = build_llm_client(config)
    parts: list[str] = []
    try:
        async for delta in client.stream_chat(
            [
                LLMMessage(role="system", content="Responde solo «OK»."),
                LLMMessage(role="user", content="Prueba de conexión"),
            ]
        ):
            parts.append(delta)
    except LLMError as exc:
        return TestOut(ok=False, detail=f"{exc.user_message} ({exc})")
    answer = " ".join("".join(parts).split())[:TEST_SAMPLE_CHARS]
    if not answer:
        return TestOut(ok=False, detail=f"El modelo «{config.model}» no ha devuelto texto")
    return TestOut(ok=True, detail=f"El modelo «{config.model}» responde: «{answer}»")


async def _check_embeddings(embeddings: Embeddings) -> TestOut:
    [vector] = await embeddings.embed(["Prueba de conexión"])
    return TestOut(
        ok=True,
        detail=(
            f"El modelo «{embeddings.model_name}» devuelve vectores de {len(vector)} dimensiones"
        ),
    )


def _ldap(request: Request) -> ConfigurableLdap:
    ldap: ConfigurableLdap = request.app.state.ldap
    return ldap


async def _check_users_change(
    request: Request,
    settings: Settings,
    current: UsersConfig,
    candidate: UsersConfig,
    changed: list[str],
) -> list[str]:
    """Comprueba que el cambio no deja la aplicación sin acceso. Devuelve avisos."""
    if not admin_enabled(settings, candidate):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Nadie podría entrar en la administración: define la cuenta local "
            "(CHATBOT_ADMIN_PASSWORD) o, con LDAP, un grupo de administración",
        )
    switching = candidate.auth_mode != current.auth_mode
    connection_changed = any(field in UsersConfig.CONNECTION_FIELDS for field in changed)
    if candidate.auth_mode == "ldap" and (switching or connection_changed):
        try:
            async with asyncio.timeout(TEST_TIMEOUT_SECONDS):
                await _ldap(request).factory(candidate).check_connection_async()
        except (DirectoryUnavailableError, TimeoutError) as exc:
            detail = str(exc) or f"sin respuesta en {TEST_TIMEOUT_SECONDS:.0f} s"
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"No se ha guardado: el directorio no responde con estos valores ({detail})",
            ) from exc
    warnings: list[str] = []
    if switching:
        warnings.append(
            "Ha cambiado el modo de acceso: las sesiones abiertas del modo anterior se "
            "cierran y los usuarios tendrán que volver a entrar."
        )
        if candidate.auth_mode == "none" and settings.admin_password is not None:
            warnings.append(
                "Los administradores del directorio ya no pueden entrar: usa la cuenta local."
            )
    return warnings


async def _check_directory(
    request: Request, config: UsersConfig, username: str, password: str
) -> TestOut:
    authenticator = _ldap(request).factory(config)
    try:
        detail = await authenticator.check_connection_async()
    except DirectoryUnavailableError as exc:
        return TestOut(ok=False, detail=f"No se puede conectar con el directorio: {exc}")
    if not username:
        return TestOut(ok=True, detail=detail)
    try:
        user = await authenticator.authenticate_async(username, password)
    except InvalidCredentialsError:
        return TestOut(
            ok=False,
            detail=f"{detail}, pero el usuario «{username}» no existe o la contraseña no es válida",
        )
    except DirectoryUnavailableError as exc:
        return TestOut(ok=False, detail=f"{detail}, pero falló la búsqueda del usuario: {exc}")
    role = "es administrador" if user.is_admin else "no es administrador"
    return TestOut(
        ok=True, detail=f"{detail}. Usuario «{user.id}» ({user.display_name}) válido; {role}"
    )
