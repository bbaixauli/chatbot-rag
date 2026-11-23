"""auditoría: ejecuciones del proceso de revisión y avisos

Revision ID: e8a4c6b2d1f9
Revises: d7f3b9a1c2e4
Create Date: 2026-11-24 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e8a4c6b2d1f9"
down_revision: str | Sequence[str] | None = "d7f3b9a1c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "audit_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger", sa.String(length=16), nullable=False),
        sa.Column("triggered_by", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("from_id", sa.Integer(), nullable=False),
        sa.Column("to_id", sa.Integer(), nullable=True),
        sa.Column("reviewed", sa.Integer(), nullable=False),
        sa.Column("reviewed_by_model", sa.Integer(), nullable=False),
        sa.Column("model_errors", sa.Integer(), nullable=False),
        sa.Column("alerts", sa.Integer(), nullable=False),
        sa.Column("anonymized", sa.Integer(), nullable=False),
        sa.Column("used_model", sa.String(length=255), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("notification_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_audit_runs_running",
        "audit_runs",
        ["status"],
        unique=True,
        sqlite_where=sa.text("status = 'running'"),
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_table(
        "audit_alerts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("question_log_id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["question_log_id"], ["question_log.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["audit_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_alerts_question_log_id", "audit_alerts", ["question_log_id"])
    op.create_index("ix_audit_alerts_run_id", "audit_alerts", ["run_id"])
    op.create_index("ix_audit_alerts_status_id", "audit_alerts", ["status", "id"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_audit_alerts_status_id", table_name="audit_alerts")
    op.drop_index("ix_audit_alerts_run_id", table_name="audit_alerts")
    op.drop_index("ix_audit_alerts_question_log_id", table_name="audit_alerts")
    op.drop_table("audit_alerts")
    op.drop_index("uq_audit_runs_running", table_name="audit_runs")
    op.drop_table("audit_runs")
