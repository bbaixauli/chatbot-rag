"""tamaño del prompt y recortes en el registro de preguntas

Revision ID: a7c3e9f1b5d2
Revises: e5a9c1d7f3b2
Create Date: 2026-11-26 20:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7c3e9f1b5d2"
down_revision: str | Sequence[str] | None = "e5a9c1d7f3b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("question_log") as batch_op:
        batch_op.add_column(sa.Column("prompt_tokens_estimated", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("prompt_tokens", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("prompt_trim", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("question_log") as batch_op:
        batch_op.drop_column("prompt_trim")
        batch_op.drop_column("prompt_tokens")
        batch_op.drop_column("prompt_tokens_estimated")
