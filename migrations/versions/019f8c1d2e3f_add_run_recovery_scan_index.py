"""add DB index for stale run recovery

Revision ID: 019f8c1d2e3f
Revises: 019f7950a1b2
Create Date: 2026-07-19 16:30:00
"""

from __future__ import annotations

from alembic import op

revision = "019f8c1d2e3f"
down_revision = "019f7950a1b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_fleet_runs_recovery_scan",
        "fleet_runs",
        ["status", "claim_heartbeat_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_fleet_runs_recovery_scan", table_name="fleet_runs")
