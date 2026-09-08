"""Enforce Sandbox Binding lineage and Session status.

Revision ID: 019fe0010001
Revises: 019fdb010001
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019fe0010001"
down_revision = "019fdb010001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    if connection.dialect.name == "postgresql":
        connection.execute(
            sa.text("LOCK TABLE fleet_workspaces, fleet_sessions, fleet_sandbox_bindings IN SHARE ROW EXCLUSIVE MODE")
        )
    invalid = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM fleet_sandbox_bindings b "
            "LEFT JOIN fleet_sessions s ON s.id = b.session_id AND s.workspace_id = b.workspace_id "
            "LEFT JOIN fleet_workspaces w ON w.id = b.workspace_id "
            "WHERE s.id IS NULL OR w.id IS NULL"
        )
    ).scalar_one()
    if invalid:
        raise RuntimeError("Sandbox Binding lineage preflight failed; repair orphaned or mismatched bindings first")
    invalid_status = connection.execute(
        sa.text("SELECT COUNT(*) FROM fleet_sessions WHERE status NOT IN ('active', 'archived') OR status IS NULL")
    ).scalar_one()
    if invalid_status:
        raise RuntimeError("Session status preflight failed; repair invalid statuses first")
    op.create_index("uq_fleet_sessions_id_workspace", "fleet_sessions", ["id", "workspace_id"], unique=True)
    with op.batch_alter_table("fleet_sessions") as batch:
        batch.create_check_constraint("ck_fleet_sessions_status", "status IN ('active', 'archived')")
    with op.batch_alter_table("fleet_sandbox_bindings") as batch:
        batch.create_foreign_key(
            "fk_fleet_bindings_workspace", "fleet_workspaces", ["workspace_id"], ["id"], ondelete="CASCADE"
        )
        batch.create_foreign_key(
            "fk_fleet_bindings_session_workspace",
            "fleet_sessions",
            ["session_id", "workspace_id"],
            ["id", "workspace_id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("fleet_sandbox_bindings") as batch:
        batch.drop_constraint("fk_fleet_bindings_session_workspace", type_="foreignkey")
        batch.drop_constraint("fk_fleet_bindings_workspace", type_="foreignkey")
    with op.batch_alter_table("fleet_sessions") as batch:
        batch.drop_constraint("ck_fleet_sessions_status", type_="check")
    op.drop_index("uq_fleet_sessions_id_workspace", table_name="fleet_sessions")
