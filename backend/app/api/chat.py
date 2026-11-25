"""Endpoint de chat con respuesta en streaming (Server-Sent Events)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.api.deps import DbSession, RuntimeConfigDep, SettingsDep
from app.api.schemas import ChatConfigOut, ChatRequest
from app.core.auth import CurrentUserDep, access_kind, chat_session_id
from app.core.rate_limit import ChatRateLimit, client_key
from app.services.chat import ChatService, ConversationNotFoundError, Requester
from app.services.chat_config import ChatConfig, ChatConfigRepository
from app.services.llm import LLMClient
from app.services.scope import ScopeError

router = APIRouter(tags=["chat"])


def get_llm(request: Request) -> LLMClient:
    """Cliente del modelo asociado a la aplicación (sustituible en tests)."""
    llm: LLMClient = request.app.state.llm
    return llm


LLMDep = Annotated[LLMClient, Depends(get_llm)]


@router.get("/chat/config", response_model=ChatConfigOut)
async def get_chat_config(db: DbSession, _: CurrentUserDep) -> ChatConfig:
    """Mensaje inicial y preguntas sugeridas configurados en la administración."""
    return await ChatConfigRepository(db).get()


@router.post(
    "/chat",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def chat(
    body: ChatRequest,
    request: Request,
    user: CurrentUserDep,
    settings: SettingsDep,
    config: RuntimeConfigDep,
    llm: LLMDep,
    _: ChatRateLimit,
) -> StreamingResponse:
    """Envía un mensaje y devuelve la respuesta como eventos SSE.

    Eventos: `conversation` (id y título), `scope` (documentos a los que se limita la
    búsqueda, si se limita), `token` (fragmento de texto), `sources` (fuentes), `done` (id
    del mensaje guardado) y `error` (detalle legible).

    `document_ids` limita la búsqueda a esos documentos y lo guarda en la conversación
    (`[]` la quita; sin indicar, se mantiene la guardada).
    """
    text = body.message.strip()
    if not text:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El mensaje está vacío")
    if len(text) > config.chat.max_message_length:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "El mensaje es demasiado largo")

    service = ChatService(
        request.app.state.sessionmaker, llm, settings, config, request.app.state.retriever
    )
    try:
        prepared = await service.prepare(
            user.id,
            body.conversation_id,
            text,
            session_id=chat_session_id(request),
            access_kind=access_kind(config.users.auth_mode, user),
            requester=Requester(
                ip=client_key(request),
                user_agent=(request.headers.get("user-agent") or "")[:300] or None,
                display_name=user.display_name,
            ),
            document_ids=body.document_ids,
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversación no encontrada") from exc
    except ScopeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc

    return StreamingResponse(
        service.stream(prepared),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
