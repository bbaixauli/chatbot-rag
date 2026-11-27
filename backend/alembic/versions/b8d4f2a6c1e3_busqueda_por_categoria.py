"""búsqueda limitada por categoría

Revision ID: b8d4f2a6c1e3
Revises: a7c3e9f1b5d2
Create Date: 2026-11-27 01:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d4f2a6c1e3"
down_revision: str | Sequence[str] | None = "a7c3e9f1b5d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.add_column(sa.Column("category_ids", sa.JSON(), nullable=True))
    with op.batch_alter_table("question_log") as batch_op:
        batch_op.add_column(sa.Column("scope_category_ids", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("question_log") as batch_op:
        batch_op.drop_column("scope_category_ids")
    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_column("category_ids")
