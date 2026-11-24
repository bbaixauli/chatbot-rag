"""preprocesado de los documentos

Revision ID: f3c9d5e7a2b1
Revises: e8a4c6b2d1f9
Create Date: 2026-11-24 19:30:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f3c9d5e7a2b1"
down_revision: str | Sequence[str] | None = "e8a4c6b2d1f9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("preprocessing", sa.String(length=64), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_column("preprocessing")
