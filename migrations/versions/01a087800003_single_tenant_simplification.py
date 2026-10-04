"""Single-tenant simplification: remove multi-tenant foreign keys and composite constraints.

Revision ID: 01a087800003
Revises: 01a087800002
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "01a087800003"
down_revision = "01a087800002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    # 1. Drop foreign keys referencing fleet_users and fleet_workspaces
    target_tables = (
        "fleet_sandbox_bindings",
        "fleet_sessions",
        "fleet_attachments",
        "fleet_artifacts",
        "fleet_memory_promotion_intents",
    )
    for table_name in target_tables:
        if table_name not in inspector.get_table_names():
            continue
        fks = inspector.get_foreign_keys(table_name)
        with op.batch_alter_table(table_name) as batch:
            for fk in fks:
                referred_table = fk.get("referred_table")
                name = fk.get("name")
                if name and (
                    referred_table in ("fleet_users", "fleet_workspaces")
                    or name in ("fk_fleet_bindings_workspace", "fk_fleet_bindings_session_workspace")
                ):
                    batch.drop_constraint(name, type_="foreignkey")

    # 2. Drop composite unique index on fleet_sessions if present
    if "fleet_sessions" in inspector.get_table_names():
        indexes = [idx["name"] for idx in inspector.get_indexes("fleet_sessions")]
        if "uq_fleet_sessions_id_workspace" in indexes:
            op.drop_index("uq_fleet_sessions_id_workspace", table_name="fleet_sessions")


def downgrade() -> None:
    connection = op.get_bind()
    inspector = sa.inspect(connection)

    if "fleet_sessions" in inspector.get_table_names():
        indexes = [idx["name"] for idx in inspector.get_indexes("fleet_sessions")]
        if "uq_fleet_sessions_id_workspace" not in indexes:
            op.create_index(
                "uq_fleet_sessions_id_workspace",
                "fleet_sessions",
                ["id", "workspace_id"],
                unique=True,
            )
    with op.batch_alter_table("fleet_sandbox_bindings") as batch:
        batch.create_foreign_key(
            "fk_fleet_bindings_workspace",
            "fleet_workspaces",
            ["workspace_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.create_foreign_key(
            "fk_fleet_bindings_session_workspace",
            "fleet_sessions",
            ["session_id", "workspace_id"],
            ["id", "workspace_id"],
            ondelete="CASCADE",
        )
