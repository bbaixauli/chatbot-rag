"""Endpoints del histórico de conversaciones."""

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.api.schemas import ConversationCreate, ConversationOut, ConversationUpdate, MessageOut
from app.core.auth import CurrentUserDep
from app.db.models import Conversation
from app.services.conversations import DEFAULT_TITLE, ConversationRepository
from app.services.scope import ScopeError, resolve_scope

router = APIRouter(prefix="/conversations", tags=["conversations"])


async def _get_owned(
    repo: ConversationRepository, conversation_id: str, user_id: str
) -> Conversation:
    conversation = await repo.get_for_user(conversation_id, user_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Conversación no encontrada")
    return conversation


@router.get("", response_model=list[ConversationOut])
async def list_conversations(db: DbSession, user: CurrentUserDep) -> list[Conversation]:
    return await ConversationRepository(db).list_for_user(user.id)


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(
    body: ConversationCreate, db: DbSession, user: CurrentUserDep
) -> Conversation:
    title = (body.title or "").strip() or DEFAULT_TITLE
    conversation = await ConversationRepository(db).create(user.id, title)
    await db.commit()
    return conversation


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(
    conversation_id: str, body: ConversationUpdate, db: DbSession, user: CurrentUserDep
) -> Conversation:
    """Cambia el título o los documentos a los que se limita la búsqueda (`[]`: todos)."""
    repo = ConversationRepository(db)
    conversation = await _get_owned(repo, conversation_id, user.id)
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, "El título no puede estar vacío"
            )
        conversation = await repo.rename(conversation, title)
    if body.document_ids is not None:
        try:
            await resolve_scope(db, conversation, body.document_ids)
        except ScopeError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await db.commit()
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, db: DbSession, user: CurrentUserDep) -> None:
    repo = ConversationRepository(db)
    await repo.delete(await _get_owned(repo, conversation_id, user.id))
    await db.commit()


@router.get("/{conversation_id}/messages", response_model=list[MessageOut])
async def list_messages(conversation_id: str, db: DbSession, user: CurrentUserDep) -> list[object]:
    repo = ConversationRepository(db)
    conversation = await _get_owned(repo, conversation_id, user.id)
    return list(await repo.list_messages(conversation.id))
