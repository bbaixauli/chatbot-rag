"""auditoría: más datos en el registro de preguntas

Revision ID: d7f3b9a1c2e4
Revises: c4a1e8d2f5b7
Create Date: 2026-11-24 09:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7f3b9a1c2e4"
down_revision: str | Sequence[str] | None = "c4a1e8d2f5b7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

COLUMNS = [
    sa.Column("client_ip", sa.String(length=45), nullable=True),
    sa.Column("user_agent", sa.String(length=300), nullable=True),
    sa.Column("user_display_name", sa.String(length=255), nullable=True),
    sa.Column("model", sa.String(length=255), nullable=True),
    sa.Column("prompt_version_id", sa.Integer(), nullable=True),
    sa.Column("search_query", sa.Text(), nullable=True),
    sa.Column("cited_documents", sa.JSON(), nullable=True),
    sa.Column("error_detail", sa.Text(), nullable=True),
]


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("question_log") as batch_op:
        for column in COLUMNS:
            batch_op.add_column(column)
    op.create_index("ix_question_log_user_id", "question_log", ["user_id"])
    op.create_index("ix_question_log_client_ip", "question_log", ["client_ip"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_question_log_client_ip", table_name="question_log")
    op.drop_index("ix_question_log_user_id", table_name="question_log")
    with op.batch_alter_table("question_log") as batch_op:
        for column in reversed(COLUMNS):
            batch_op.drop_column(column.name)
