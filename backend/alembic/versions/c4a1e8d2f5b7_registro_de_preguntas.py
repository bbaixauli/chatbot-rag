"""registro de preguntas para el cuadro de mando

Crea `question_log` y lo rellena con las conversaciones existentes: cada pregunta con la
respuesta que la sigue. Para el histórico, el tiempo de respuesta es la diferencia entre
la hora de la pregunta y la de la respuesta guardada, y la sesión es la conversación.

Revision ID: c4a1e8d2f5b7
Revises: b7d2e4f1a903
Create Date: 2026-11-23 10:00:00.000000

"""

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4a1e8d2f5b7"
down_revision: str | Sequence[str] | None = "b7d2e4f1a903"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ANONYMOUS_USER_ID = "anonymous"
ANONYMOUS_PREFIX = "anon:"


def _as_utc(value: datetime | str) -> datetime:
    if isinstance(value, str):  # SQLite sin conversión de tipos
        value = datetime.fromisoformat(value)
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _access_kind(user_id: str | None) -> str:
    if user_id is None or user_id == ANONYMOUS_USER_ID:
        return "shared"
    if user_id.startswith(ANONYMOUS_PREFIX):
        # En el histórico no se distingue el acceso anónimo con LDAP de la sesión privada.
        return "anonymous"
    return "ldap"


def upgrade() -> None:
    """Upgrade schema."""
    question_log = op.create_table(
        "question_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("user_id", sa.String(length=255), nullable=True),
        sa.Column("access_kind", sa.String(length=16), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=True),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("no_info", sa.Boolean(), nullable=False),
        sa.Column("first_token_ms", sa.Integer(), nullable=True),
        sa.Column("response_ms", sa.Integer(), nullable=True),
        sa.Column("sources_retrieved", sa.Integer(), nullable=False),
        sa.Column("sources_cited", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_question_log_asked_at", "question_log", ["asked_at"])
    op.create_index("ix_question_log_conversation_id", "question_log", ["conversation_id"])

    bind = op.get_bind()
    messages = bind.execute(
        sa.text(
            "SELECT m.id, m.conversation_id, m.role, m.content, m.created_at, c.user_id "
            "FROM messages m JOIN conversations c ON c.id = m.conversation_id "
            "ORDER BY m.conversation_id, m.id"
        )
    ).all()
    # Fuentes guardadas de cada respuesta: total y citadas.
    sources = {
        row[0]: (row[1], row[2] or 0)
        for row in bind.execute(
            sa.text(
                "SELECT message_id, COUNT(*), SUM(CASE WHEN cited THEN 1 ELSE 0 END) "
                "FROM message_sources GROUP BY message_id"
            )
        )
    }
    rows = []
    for index, message in enumerate(messages):
        if message.role != "user":
            continue
        following = messages[index + 1] if index + 1 < len(messages) else None
        answered = (
            following is not None
            and following.conversation_id == message.conversation_id
            and following.role == "assistant"
        )
        asked_at = _as_utc(message.created_at)
        retrieved, cited = sources.get(following.id, (0, 0)) if answered else (0, 0)
        rows.append(
            {
                "asked_at": asked_at,
                "session_id": message.conversation_id,
                "user_id": message.user_id,
                "access_kind": _access_kind(message.user_id),
                "conversation_id": message.conversation_id,
                "question": message.content,
                "answer": following.content if answered else None,
                "status": "ok" if answered else "error",
                "no_info": False,  # se calcula al arrancar con las frases configuradas
                "first_token_ms": None,
                "response_ms": max(
                    0, int((_as_utc(following.created_at) - asked_at).total_seconds() * 1000)
                )
                if answered
                else None,
                "sources_retrieved": retrieved,
                "sources_cited": cited,
            }
        )
    if rows:
        op.bulk_insert(question_log, rows)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_question_log_conversation_id", table_name="question_log")
    op.drop_index("ix_question_log_asked_at", table_name="question_log")
    op.drop_table("question_log")
