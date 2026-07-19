"""add Turn settling recovery state

Revision ID: 019f7950a1b2
Revises: 019f5b3c96bd
Create Date: 2026-07-19 11:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019f7950a1b2"
down_revision = "019f5b3c96bd"
branch_labels = None
depends_on = None

_OLD_TERMINAL_SHAPE = (
    "(status = 'running' AND claim_owner IS NOT NULL AND claim_heartbeat_at IS NOT NULL "
    "AND commit_checkpoint_version IS NULL AND failure_code IS NULL) OR "
    "(status = 'completed' AND claim_owner IS NULL AND commit_checkpoint_version IS NOT NULL "
    "AND failure_code IS NULL) OR "
    "(status IN ('failed', 'cancelled', 'timeout') AND claim_owner IS NULL "
    "AND commit_checkpoint_version IS NULL AND failure_code IS NOT NULL)"
)

_SETTLING_TERMINAL_SHAPE = (
    "(status = 'running' AND claim_owner IS NOT NULL AND claim_heartbeat_at IS NOT NULL "
    "AND commit_checkpoint_version IS NULL AND failure_code IS NULL) OR "
    "(status = 'settling' AND claim_owner IS NOT NULL AND claim_heartbeat_at IS NOT NULL "
    "AND commit_checkpoint_version IS NULL AND failure_code IS NOT NULL AND terminal_intent IS NOT NULL) OR "
    "(status = 'completed' AND claim_owner IS NULL AND commit_checkpoint_version IS NOT NULL "
    "AND failure_code IS NULL) OR "
    "(status IN ('failed', 'cancelled', 'timeout') AND claim_owner IS NULL "
    "AND commit_checkpoint_version IS NULL AND failure_code IS NOT NULL)"
)


def upgrade() -> None:
    with op.batch_alter_table("fleet_runs") as batch:
        batch.drop_index("uq_fleet_runs_live_idempotency")
        batch.drop_index("uq_fleet_runs_one_running")
        batch.drop_constraint("ck_fleet_runs_terminal_shape", type_="check")
        batch.drop_constraint("ck_fleet_runs_status", type_="check")
        batch.add_column(sa.Column("terminal_intent", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("recovery_metadata_json", sa.JSON(), nullable=True))
        batch.create_check_constraint(
            "ck_fleet_runs_status",
            "status IN ('running', 'settling', 'completed', 'failed', 'cancelled', 'timeout')",
        )
        batch.create_check_constraint("ck_fleet_runs_terminal_shape", _SETTLING_TERMINAL_SHAPE)
        batch.create_index(
            "uq_fleet_runs_live_idempotency",
            ("session_id", "idempotency_key"),
            unique=True,
            sqlite_where=sa.text("status IN ('running', 'settling', 'completed')"),
            postgresql_where=sa.text("status IN ('running', 'settling', 'completed')"),
        )
        batch.create_index(
            "uq_fleet_runs_one_running",
            ("session_id",),
            unique=True,
            sqlite_where=sa.text("status IN ('running', 'settling')"),
            postgresql_where=sa.text("status IN ('running', 'settling')"),
        )


def downgrade() -> None:
    with op.batch_alter_table("fleet_runs") as batch:
        batch.drop_index("uq_fleet_runs_live_idempotency")
        batch.drop_index("uq_fleet_runs_one_running")
        batch.drop_constraint("ck_fleet_runs_terminal_shape", type_="check")
        batch.drop_constraint("ck_fleet_runs_status", type_="check")
        batch.drop_column("recovery_metadata_json")
        batch.drop_column("terminal_intent")
        batch.create_check_constraint(
            "ck_fleet_runs_status",
            "status IN ('running', 'completed', 'failed', 'cancelled', 'timeout')",
        )
        batch.create_check_constraint("ck_fleet_runs_terminal_shape", _OLD_TERMINAL_SHAPE)
        batch.create_index(
            "uq_fleet_runs_live_idempotency",
            ("session_id", "idempotency_key"),
            unique=True,
            sqlite_where=sa.text("status IN ('running', 'completed')"),
            postgresql_where=sa.text("status IN ('running', 'completed')"),
        )
        batch.create_index(
            "uq_fleet_runs_one_running",
            ("session_id",),
            unique=True,
            sqlite_where=sa.text("status = 'running'"),
            postgresql_where=sa.text("status = 'running'"),
        )
