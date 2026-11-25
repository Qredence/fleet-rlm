"""Track session lessons proposed from completed turns."""

import sqlalchemy as sa
from alembic import op

revision = "0007_turn_lessons"
down_revision = "0006_workspace_recovery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("memories", sa.Column("source_run_id", sa.Uuid()))
    op.add_column("memories", sa.Column("skill_name", sa.Text()))
    op.create_foreign_key(
        "fk_memories_source_run_id",
        "memories",
        "runs",
        ["source_run_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint("uq_memories_source_run_id", "memories", ["source_run_id"])


def downgrade() -> None:
    op.drop_constraint("uq_memories_source_run_id", "memories", type_="unique")
    op.drop_constraint("fk_memories_source_run_id", "memories", type_="foreignkey")
    op.drop_column("memories", "skill_name")
    op.drop_column("memories", "source_run_id")
