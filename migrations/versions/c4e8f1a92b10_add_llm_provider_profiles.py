"""add_llm_provider_profiles

Revision ID: c4e8f1a92b10
Revises: bda61ca6b15e
Create Date: 2026-06-11 12:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c4e8f1a92b10"
down_revision = "bda61ca6b15e"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_provider_profiles",
        sa.Column("id", sa.UUID(), server_default=sa.text("app.uuid_v7()"), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("provider_type", sa.String(length=64), nullable=False),
        sa.Column("api_base", sa.Text(), nullable=True),
        sa.Column("api_key_ciphertext", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "llm_role_bindings",
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("profile_id", sa.UUID(), nullable=True),
        sa.Column("model_id", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["profile_id"], ["llm_provider_profiles.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("role"),
    )


def downgrade() -> None:
    op.drop_table("llm_role_bindings")
    op.drop_table("llm_provider_profiles")
