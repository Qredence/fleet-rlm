"""Drop non-unique indexes that duplicate unique constraints.

Revision ID: 0011_drop_redundant_indexes
Revises: 0010_target_postgres_schema
Create Date: 2026-04-27
"""

from __future__ import annotations

from alembic import op

revision = "0011_drop_redundant_indexes"
down_revision = "0010_target_postgres_schema"
branch_labels = None
depends_on = None

_REDUNDANT_INDEXES = [
    "ix_dataset_examples_dataset_row_index",
    "ix_execution_events_run_sequence",
    "ix_execution_steps_run_step",
]


def upgrade() -> None:
    for index_name in _REDUNDANT_INDEXES:
        op.drop_index(index_name)


def downgrade() -> None:
    op.create_index(
        "ix_dataset_examples_dataset_row_index",
        "dataset_examples",
        ["dataset_id", "row_index"],
    )
    op.create_index(
        "ix_execution_events_run_sequence",
        "execution_events",
        ["run_id", "sequence"],
    )
    op.create_index(
        "ix_execution_steps_run_step",
        "execution_steps",
        ["run_id", "step_index"],
    )
