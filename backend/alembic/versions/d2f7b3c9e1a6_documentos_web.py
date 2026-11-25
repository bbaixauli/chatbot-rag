"""documentos añadidos por su dirección web

Revision ID: d2f7b3c9e1a6
Revises: c8e2a6f4b9d1
Create Date: 2026-11-25 22:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2f7b3c9e1a6"
down_revision: str | Sequence[str] | None = "c8e2a6f4b9d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("source_url", sa.String(length=2048), nullable=True))
        batch_op.add_column(sa.Column("source_etag", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("source_last_modified", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("source_last_modified")
        batch_op.drop_column("source_etag")
        batch_op.drop_column("source_url")
