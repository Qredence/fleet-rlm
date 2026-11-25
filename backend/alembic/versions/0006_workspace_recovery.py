"""Preserve pending Daytona and object writes across worker failures."""

import sqlalchemy as sa
from alembic import op

revision = "0006_workspace_recovery"
down_revision = "0005_sandbox_cleanup"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("pending_sandbox_name", sa.Text()))
    op.create_table(
        "workspace_object_cleanup",
        sa.Column("object_key", sa.Text(), primary_key=True),
        sa.Column(
            "session_id",
            sa.Uuid(),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("upload_id", sa.Text()),
        sa.Column("run_id", sa.Uuid()),
    )
    op.create_index(
        "ix_workspace_object_cleanup_session_id", "workspace_object_cleanup", ["session_id"]
    )


def downgrade() -> None:
    op.drop_table("workspace_object_cleanup")
    op.drop_column("sessions", "pending_sandbox_name")
