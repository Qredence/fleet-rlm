"""optimize_execution_steps_indexes

Reviewed execution_steps (largest table at 3.6MB) for index optimization.

Findings:
- execution_steps is an append-only log (no status column — status lives on
  execution_runs). The existing ix_execution_steps_workspace_type_created_at
  (160KB) covers the main query pattern well.
- uq_execution_steps_tenant_workspace_id (136KB, 0 scans) is a unique constraint
  supporting compound FK scoping for RLS — kept for data integrity.
- No partial index warranted: step_type enum values are not selective enough,
  and there is no status column to filter on.

This migration is intentionally a no-op. It serves as a checkpoint documenting
the index review decision so future reviewers know the analysis was done.

If execution_steps grows beyond ~50MB, reconsider adding:
  - A BRIN index on created_at (cheap for append-only tables)
  - Partitioning by created_at (monthly ranges)

Revision ID: g3c4d5e6f7g8
Revises: f2b3c4d5e6f7
Create Date: 2026-06-25 12:30:00.000000
"""

from __future__ import annotations

revision = "g3c4d5e6f7g8"
down_revision = "f2b3c4d5e6f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # No-op: existing indexes are adequate for current table size and query patterns.
    # See module docstring for the analysis that led to this decision.
    pass


def downgrade() -> None:
    pass
