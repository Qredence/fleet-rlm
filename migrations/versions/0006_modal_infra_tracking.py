"""Add Modal infrastructure tracking and run step correlations.

Revision ID: 0006_modal_infra_tracking
Revises: 0005_rlm_programs_and_traces
Create Date: 2026-02-23
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0006_modal_infra_tracking"
down_revision = "0005_rlm_programs_and_traces"
branch_labels = None
depends_on = None

RLS_TABLES = ["modal_volumes"]


def _enable_rls(table_name: str) -> None:
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY tenant_isolation_{table_name}
        ON {table_name}
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )


def upgrade() -> None:
    op.create_table(
        "modal_volumes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "provider",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'modal'"),
        ),
        sa.Column("volume_name", sa.String(length=255), nullable=False),
        sa.Column("external_volume_id", sa.String(length=255), nullable=True),
        sa.Column("environment", sa.String(length=64), nullable=True),
        sa.Column("region", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_modal_volumes_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "volume_name", name="uq_modal_volumes_tenant_volume_name"
        ),
    )
    op.create_index(
        "ix_modal_volumes_tenant_created_at",
        "modal_volumes",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_modal_volumes_tenant_last_seen_at",
        "modal_volumes",
        ["tenant_id", "last_seen_at"],
    )

    op.add_column(
        "run_steps",
        sa.Column("sandbox_session_external_id", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "run_steps",
        sa.Column("modal_volume_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "run_steps",
        sa.Column("modal_volume_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "run_steps", sa.Column("cost_usd_micros", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "fk_run_steps_tenant_modal_volume__modal_volumes_tenant_id_id",
        "run_steps",
        "modal_volumes",
        ["tenant_id", "modal_volume_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )

    for table_name in RLS_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(RLS_TABLES):
        op.execute(
            f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name}"
        )

    op.drop_constraint(
        "fk_run_steps_tenant_modal_volume__modal_volumes_tenant_id_id",
        "run_steps",
        type_="foreignkey",
    )
    op.drop_column("run_steps", "cost_usd_micros")
    op.drop_column("run_steps", "modal_volume_name")
    op.drop_column("run_steps", "modal_volume_id")
    op.drop_column("run_steps", "sandbox_session_external_id")

    op.drop_index("ix_modal_volumes_tenant_last_seen_at", table_name="modal_volumes")
    op.drop_index("ix_modal_volumes_tenant_created_at", table_name="modal_volumes")
    op.drop_table("modal_volumes")
