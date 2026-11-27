"""fallo de la búsqueda en la documentación

Revision ID: c9f4a2e6b8d3
Revises: b8d4f2a6c1e3
Create Date: 2026-11-27 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9f4a2e6b8d3"
down_revision: str | Sequence[str] | None = "b8d4f2a6c1e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(
            sa.Column("retrieval_failed", sa.Boolean(), server_default=sa.false(), nullable=False)
        )
    with op.batch_alter_table("question_log") as batch_op:
        batch_op.add_column(
            sa.Column("retrieval_failed", sa.Boolean(), server_default=sa.false(), nullable=False)
        )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("question_log") as batch_op:
        batch_op.drop_column("retrieval_failed")
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("retrieval_failed")
