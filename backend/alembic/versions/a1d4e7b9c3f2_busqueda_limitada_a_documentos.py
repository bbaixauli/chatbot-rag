"""búsqueda limitada a documentos

Revision ID: a1d4e7b9c3f2
Revises: f3c9d5e7a2b1
Create Date: 2026-11-25 10:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1d4e7b9c3f2"
down_revision: str | Sequence[str] | None = "f3c9d5e7a2b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("document_ids", sa.JSON(), nullable=True))
    with op.batch_alter_table("question_log") as batch_op:
        batch_op.add_column(sa.Column("scope_document_ids", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("scope_source", sa.String(length=16), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("question_log") as batch_op:
        batch_op.drop_column("scope_source")
        batch_op.drop_column("scope_document_ids")
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("document_ids")
