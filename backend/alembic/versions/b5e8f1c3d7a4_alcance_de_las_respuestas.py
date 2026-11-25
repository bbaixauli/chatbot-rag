"""alcance de las respuestas

Revision ID: b5e8f1c3d7a4
Revises: a1d4e7b9c3f2
Create Date: 2026-11-25 18:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b5e8f1c3d7a4"
down_revision: str | Sequence[str] | None = "a1d4e7b9c3f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("scope", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("scope")
