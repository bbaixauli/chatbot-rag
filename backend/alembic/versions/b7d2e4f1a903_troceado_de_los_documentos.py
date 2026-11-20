"""troceado de los documentos

Revision ID: b7d2e4f1a903
Revises: 612121a88d09
Create Date: 2026-11-20 20:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7d2e4f1a903"
down_revision: str | Sequence[str] | None = "612121a88d09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("chunk_size_chars", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("chunk_overlap_chars", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("chunk_overlap_chars")
        batch_op.drop_column("chunk_size_chars")
