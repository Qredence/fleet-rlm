"""Enforce Turn-to-Run Session lineage.

Revision ID: 019fdb010001
Revises: 019fa2e4b7c1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019fdb010001"
down_revision = "019fa2e4b7c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Enforce matching run and session references for all turns.

    Raises:
        RuntimeError: If any turn lacks a run with the same ID and session ID.
    """
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(sa.text("LOCK TABLE fleet_runs, fleet_turns IN SHARE ROW EXCLUSIVE MODE"))
    # Check before any DDL. Never delete or repair operator data implicitly.
    invalid = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM fleet_turns t LEFT JOIN fleet_runs r "
            "ON r.id = t.run_id AND r.session_id = t.session_id WHERE r.id IS NULL"
        )
    ).scalar_one()
    if invalid:
        raise RuntimeError("Turn/Run Session lineage preflight failed; repair orphaned or mismatched Turns first")
    # A unique index is a valid composite FK parent on SQLite and PostgreSQL,
    # and avoids rebuilding fleet_runs while other tables reference it.
    op.create_index("uq_fleet_runs_id_session", "fleet_runs", ["id", "session_id"], unique=True)
    with op.batch_alter_table("fleet_turns") as batch:
        batch.create_foreign_key(
            "fk_fleet_turns_run_session",
            "fleet_runs",
            ["run_id", "session_id"],
            ["id", "session_id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    """
    Remove the composite turn-to-run foreign key and its supporting unique index.
    """
    with op.batch_alter_table("fleet_turns") as batch:
        batch.drop_constraint("fk_fleet_turns_run_session", type_="foreignkey")
    op.drop_index("uq_fleet_runs_id_session", table_name="fleet_runs")
