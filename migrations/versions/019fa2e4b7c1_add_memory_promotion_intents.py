"""add crash-recoverable memory promotion intents (P23)

Revision ID: 019fa2e4b7c1
Revises: 019f8c1d2e3f
Create Date: 2026-08-17 07:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "019fa2e4b7c1"
down_revision = "019f8c1d2e3f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "fleet_memory_promotion_intents",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "run_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("fleet_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "session_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("fleet_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "workspace_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("fleet_workspaces.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("fleet_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("candidate_ordinal", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.String(length=12), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("learning", sa.Text(), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.String(length=8), nullable=True),
        sa.Column("memory_id", sa.String(length=8), nullable=False),
        sa.Column("record_text", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("completion_reason", sa.String(length=32), nullable=True),
        sa.Column("promoted_memory_id", sa.String(length=8), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_error", sa.String(length=64), nullable=True),
        sa.Column("claim_owner", sa.String(length=128), nullable=True),
        sa.Column("claim_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("run_id", "candidate_id", name="uq_fleet_memory_intents_run_candidate"),
        sa.CheckConstraint(
            "status IN ('pending', 'completing', 'completed', 'failed')",
            name="ck_fleet_memory_intents_status",
        ),
        sa.CheckConstraint("length(candidate_id) = 12", name="ck_fleet_memory_intents_candidate_id"),
        sa.CheckConstraint("length(memory_id) = 8", name="ck_fleet_memory_intents_memory_id"),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR length(supersedes_id) = 8",
            name="ck_fleet_memory_intents_supersedes",
        ),
        sa.CheckConstraint(
            "byte_size >= 0 AND byte_size <= 3904",
            name="ck_fleet_memory_intents_byte_size",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_fleet_memory_intents_attempts"),
        sa.CheckConstraint("source IN ('agent_candidate')", name="ck_fleet_memory_intents_source"),
    )
    op.create_index(
        "ix_fleet_memory_intents_claim_scan",
        "fleet_memory_promotion_intents",
        ["status", "next_attempt_at"],
    )
    op.create_index(
        "ix_fleet_memory_intents_run",
        "fleet_memory_promotion_intents",
        ["run_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_fleet_memory_intents_run", table_name="fleet_memory_promotion_intents")
    op.drop_index("ix_fleet_memory_intents_claim_scan", table_name="fleet_memory_promotion_intents")
    op.drop_table("fleet_memory_promotion_intents")
