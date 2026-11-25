"""título y categorías de los documentos

Revision ID: c8e2a6f4b9d1
Revises: b5e8f1c3d7a4
Create Date: 2026-11-25 21:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8e2a6f4b9d1"
down_revision: str | Sequence[str] | None = "b5e8f1c3d7a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "categories",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    with op.batch_alter_table("documents") as batch_op:
        batch_op.add_column(sa.Column("title", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("category_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("indexed_title", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("indexed_category", sa.String(length=100), nullable=True))
        batch_op.create_index(batch_op.f("ix_documents_category_id"), ["category_id"])
        batch_op.create_foreign_key(
            "fk_documents_category_id_categories",
            "categories",
            ["category_id"],
            ["id"],
            ondelete="SET NULL",
        )
    # Los documentos existentes toman como título su nombre de fichero. Se indexaron con
    # él (sin extensión) y sin categoría: no quedan desactualizados.
    op.execute("UPDATE documents SET title = filename")
    op.execute("UPDATE documents SET indexed_title = filename WHERE status = 'ready'")
    with op.batch_alter_table("documents") as batch_op:
        batch_op.alter_column("title", existing_type=sa.String(length=255), nullable=False)


def downgrade() -> None:
    """Downgrade schema."""
    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint("fk_documents_category_id_categories", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_documents_category_id"))
        batch_op.drop_column("indexed_category")
        batch_op.drop_column("indexed_title")
        batch_op.drop_column("category_id")
        batch_op.drop_column("title")
    op.drop_table("categories")
