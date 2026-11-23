"""Add foreign-key and query indexes for memories and runs."""

import sqlalchemy as sa
from alembic import op

revision = "0003_fk_and_query_indexes"
down_revision = "0002_workspace_settings"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("idx_memories_session_id", "memories", ["session_id"])
    op.create_index("idx_runs_session_id", "runs", ["session_id"])
    op.create_index(
        "idx_runs_parent_run_id",
        "runs",
        ["parent_run_id"],
        postgresql_where=sa.text("parent_run_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "idx_runs_parent_run_id",
        table_name="runs",
        postgresql_where=sa.text("parent_run_id IS NOT NULL"),
    )
    op.drop_index("idx_runs_session_id", table_name="runs")
    op.drop_index("idx_memories_session_id", table_name="memories")
