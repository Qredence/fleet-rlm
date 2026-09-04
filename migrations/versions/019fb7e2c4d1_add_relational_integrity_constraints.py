"""add relational integrity constraints

Revision ID: 019fb7e2c4d1
Revises: 019fa2e4b7c1
Create Date: 2026-09-04 00:00:00
"""

from __future__ import annotations

from alembic import op

revision = "019fb7e2c4d1"
down_revision = "019fa2e4b7c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("fleet_sessions") as batch:
        batch.create_check_constraint("ck_fleet_sessions_status", "status IN ('active', 'archived')")
        batch.create_unique_constraint("uq_fleet_sessions_id_workspace", ("id", "workspace_id"))
    with op.batch_alter_table("fleet_turns") as batch:
        batch.create_foreign_key("fk_fleet_turns_run_id", "fleet_runs", ["run_id"], ["id"], ondelete="CASCADE")
    with op.batch_alter_table("fleet_sandbox_bindings") as batch:
        batch.create_foreign_key(
            "fk_fleet_sandbox_bindings_workspace_id",
            "fleet_workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_fleet_sandbox_bindings_session_workspace",
            "fleet_sessions",
            ["session_id", "workspace_id"],
            ["id", "workspace_id"],
            ondelete="CASCADE",
        )
        batch.create_check_constraint(
            "ck_fleet_sandbox_bindings_provider_state",
            "provider_state IN ('missing', 'running', 'stopped', 'paused', 'archived', "
            "'fencing', 'quarantined', 'unrecoverable')",
        )


def downgrade() -> None:
    with op.batch_alter_table("fleet_sandbox_bindings") as batch:
        batch.drop_constraint("ck_fleet_sandbox_bindings_provider_state", type_="check")
        batch.drop_constraint("fk_fleet_sandbox_bindings_session_workspace", type_="foreignkey")
        batch.drop_constraint("fk_fleet_sandbox_bindings_workspace_id", type_="foreignkey")
    with op.batch_alter_table("fleet_turns") as batch:
        batch.drop_constraint("fk_fleet_turns_run_id", type_="foreignkey")
    with op.batch_alter_table("fleet_sessions") as batch:
        batch.drop_constraint("uq_fleet_sessions_id_workspace", type_="unique")
        batch.drop_constraint("ck_fleet_sessions_status", type_="check")
