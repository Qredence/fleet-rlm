"""Remove legacy Modal persistence surface from the control-plane schema.

Revision ID: 0009_remove_modal_runtime_surface
Revises: 0008_neon_control_plane_consolidation
Create Date: 2026-04-06
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0009_remove_modal_runtime_surface"
down_revision = "0008_neon_control_plane_consolidation"
branch_labels = None
depends_on = None


def _rebuild_sandbox_provider_enum(*, include_modal: bool) -> None:
    values = ["daytona", "aca_jobs", "local"]
    if include_modal:
        values = ["modal", *values]
    values_sql = ", ".join(f"'{value}'" for value in values)
    tmp_name = "sandbox_provider_new"

    op.execute(f"CREATE TYPE {tmp_name} AS ENUM ({values_sql})")
    op.execute(
        """
        ALTER TABLE sandbox_sessions
        ALTER COLUMN provider TYPE sandbox_provider_new
        USING provider::text::sandbox_provider_new
        """
    )
    op.execute(
        """
        ALTER TABLE runs
        ALTER COLUMN sandbox_provider TYPE sandbox_provider_new
        USING CASE
            WHEN sandbox_provider IS NULL THEN NULL
            ELSE sandbox_provider::text::sandbox_provider_new
        END
        """
    )
    op.execute("DROP TYPE sandbox_provider")
    op.execute(f"ALTER TYPE {tmp_name} RENAME TO sandbox_provider")


def upgrade() -> None:
    op.execute(
        """
        UPDATE sandbox_sessions
        SET provider = 'daytona'
        WHERE provider::text = 'modal'
        """
    )
    op.execute(
        """
        UPDATE runs
        SET sandbox_provider = 'daytona'
        WHERE sandbox_provider::text = 'modal'
        """
    )

    op.drop_index(
        "ix_run_steps_tenant_modal_volume_created_at",
        table_name="run_steps",
        if_exists=True,
    )
    op.drop_constraint(
        "fk_run_steps_tenant_modal_volume__modal_volumes_tenant_id_id",
        "run_steps",
        type_="foreignkey",
    )
    op.drop_column("run_steps", "modal_volume_name")
    op.drop_column("run_steps", "modal_volume_id")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_modal_volumes ON modal_volumes")
    op.drop_index(
        "ix_modal_volumes_tenant_provider_env_last_seen",
        table_name="modal_volumes",
        if_exists=True,
    )
    op.drop_index(
        "ix_modal_volumes_tenant_last_seen_at",
        table_name="modal_volumes",
        if_exists=True,
    )
    op.drop_index(
        "ix_modal_volumes_tenant_created_at",
        table_name="modal_volumes",
        if_exists=True,
    )
    op.drop_table("modal_volumes")

    _rebuild_sandbox_provider_enum(include_modal=False)


def downgrade() -> None:
    _rebuild_sandbox_provider_enum(include_modal=True)

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
    op.create_index(
        "ix_modal_volumes_tenant_provider_env_last_seen",
        "modal_volumes",
        ["tenant_id", "provider", "environment", "last_seen_at"],
    )
    op.execute("ALTER TABLE modal_volumes ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE modal_volumes FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY tenant_isolation_modal_volumes
        ON modal_volumes
        USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
        WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid)
        """
    )

    op.add_column(
        "run_steps",
        sa.Column("modal_volume_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "run_steps",
        sa.Column("modal_volume_name", sa.String(length=255), nullable=True),
    )
    op.create_foreign_key(
        "fk_run_steps_tenant_modal_volume__modal_volumes_tenant_id_id",
        "run_steps",
        "modal_volumes",
        ["tenant_id", "modal_volume_id"],
        ["tenant_id", "id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_run_steps_tenant_modal_volume_created_at",
        "run_steps",
        ["tenant_id", "modal_volume_id", "created_at"],
    )
