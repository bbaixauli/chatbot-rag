"""actualización periódica de los documentos web

Revision ID: e5a9c1d7f3b2
Revises: d2f7b3c9e1a6
Create Date: 2026-11-25 23:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5a9c1d7f3b2"
down_revision: str | Sequence[str] | None = "d2f7b3c9e1a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(
            sa.Column("source_checked_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("source_changed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(sa.Column("source_error", sa.Text(), nullable=True))
        batch_op.add_column(
            sa.Column("source_not_found", sa.Integer(), server_default="0", nullable=False)
        )
    # Los documentos web ya existentes se dan por comprobados al descargarlos.
    op.execute(
        "UPDATE documents SET source_checked_at = created_at, source_changed_at = created_at "
        "WHERE source_url IS NOT NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("source_not_found")
        batch_op.drop_column("source_error")
        batch_op.drop_column("source_changed_at")
        batch_op.drop_column("source_checked_at")
