"""caracteres por token de la estimación del prompt

Revision ID: d4b8e2f6a1c9
Revises: c9f4a2e6b8d3
Create Date: 2026-11-28 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d4b8e2f6a1c9"
down_revision: str | Sequence[str] | None = "c9f4a2e6b8d3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Nulo en las preguntas anteriores: se estimaron con 3,5 caracteres por token.
    with op.batch_alter_table("question_log") as batch_op:
        batch_op.add_column(sa.Column("prompt_chars_per_token", sa.Float(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("question_log") as batch_op:
        batch_op.drop_column("prompt_chars_per_token")
