"""drop_orphaned_outbox_and_volume_tables

Revision ID: bda61ca6b15e
Revises: 0012_updated_at_trigger
Create Date: 2026-05-22 22:53:30.277181

Drop the three ORM-orphaned tables that have no callers in the codebase:
- outbox_events (OutboxEvent model removed)
- workspace_volumes (WorkspaceVolume model removed)
- volume_objects (VolumeObject model removed, FK to workspace_volumes)
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "bda61ca6b15e"
down_revision = "0012_updated_at_trigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop volume_objects first (FK → workspace_volumes)
    op.drop_index(op.f("ix_volume_objects_workspace_path"), table_name="volume_objects")
    op.drop_index(op.f("ix_volume_objects_workspace_volume_modified"), table_name="volume_objects")
    op.drop_table("volume_objects")

    # Drop workspace_volumes
    op.drop_index(op.f("ix_workspace_volumes_workspace_status"), table_name="workspace_volumes")
    op.drop_index(op.f("ix_workspace_volumes_workspace_updated_at"), table_name="workspace_volumes")
    op.drop_table("workspace_volumes")

    # Drop outbox_events (standalone, no FK dependencies from live tables)
    op.drop_index(op.f("ix_outbox_events_status_available_workspace"), table_name="outbox_events")
    op.drop_index(op.f("ix_outbox_events_workspace_created_at"), table_name="outbox_events")
    op.drop_table("outbox_events")


def downgrade() -> None:
    # Recreate outbox_events
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.UUID(), server_default=sa.text("app.uuid_v7()"), autoincrement=False, nullable=False),
        sa.Column("tenant_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("workspace_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("aggregate_type", sa.VARCHAR(length=128), autoincrement=False, nullable=False),
        sa.Column("aggregate_id", sa.UUID(), autoincrement=False, nullable=True),
        sa.Column("event_type", sa.VARCHAR(length=128), autoincrement=False, nullable=False),
        sa.Column(
            "payload_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM("pending", "dispatched", "failed", name="outbox_status"),
            server_default=sa.text("'pending'::outbox_status"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "available_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("attempts", sa.INTEGER(), server_default=sa.text("0"), autoincrement=False, nullable=False),
        sa.Column("last_error", postgresql.JSONB(astext_type=sa.Text()), autoincrement=False, nullable=True),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name=op.f("fk_outbox_events_tenant_workspace__workspaces_tenant_id_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("outbox_events_tenant_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("outbox_events_pkey")),
    )
    op.create_index(
        op.f("ix_outbox_events_workspace_created_at"), "outbox_events", ["workspace_id", "created_at"], unique=False
    )
    op.create_index(
        op.f("ix_outbox_events_status_available_workspace"),
        "outbox_events",
        ["status", "available_at", "workspace_id"],
        unique=False,
    )

    # Recreate workspace_volumes
    op.create_table(
        "workspace_volumes",
        sa.Column("id", sa.UUID(), server_default=sa.text("app.uuid_v7()"), autoincrement=False, nullable=False),
        sa.Column("tenant_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("workspace_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column(
            "provider",
            postgresql.ENUM("daytona", "aca_jobs", "local", name="sandbox_provider"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("external_volume_id", sa.VARCHAR(length=255), autoincrement=False, nullable=True),
        sa.Column("external_volume_name", sa.VARCHAR(length=255), autoincrement=False, nullable=True),
        sa.Column("mount_path", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column("root_path", sa.TEXT(), autoincrement=False, nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM("provisioning", "ready", "error", "archived", name="workspace_volume_status"),
            server_default=sa.text("'provisioning'::workspace_volume_status"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name=op.f("fk_workspace_volumes_tenant_workspace__workspaces_tenant_id_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("workspace_volumes_tenant_id_fkey"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("workspace_volumes_pkey")),
        sa.UniqueConstraint(
            "workspace_id",
            "external_volume_id",
            name=op.f("uq_workspace_volumes_workspace_external_id"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_workspace_volumes_workspace_updated_at"),
        "workspace_volumes",
        ["workspace_id", "updated_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workspace_volumes_workspace_status"), "workspace_volumes", ["workspace_id", "status"], unique=False
    )

    # Recreate volume_objects (FK → workspace_volumes)
    op.create_table(
        "volume_objects",
        sa.Column("id", sa.UUID(), server_default=sa.text("app.uuid_v7()"), autoincrement=False, nullable=False),
        sa.Column("tenant_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("workspace_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("workspace_volume_id", sa.UUID(), autoincrement=False, nullable=False),
        sa.Column("path", sa.TEXT(), autoincrement=False, nullable=False),
        sa.Column(
            "object_type",
            postgresql.ENUM("file", "directory", name="volume_object_type"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column("mime_type", sa.VARCHAR(length=255), autoincrement=False, nullable=True),
        sa.Column("size_bytes", sa.BIGINT(), autoincrement=False, nullable=True),
        sa.Column("checksum", sa.VARCHAR(length=255), autoincrement=False, nullable=True),
        sa.Column("modified_at", postgresql.TIMESTAMP(timezone=True), autoincrement=False, nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text("now()"),
            autoincrement=False,
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspaces.tenant_id", "workspaces.id"],
            name=op.f("fk_volume_objects_tenant_workspace__workspaces_tenant_id_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("volume_objects_tenant_id_fkey"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["workspace_volume_id"],
            ["workspace_volumes.id"],
            name=op.f("fk_volume_objects_workspace_volume_id__workspace_volumes_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("volume_objects_pkey")),
        sa.UniqueConstraint(
            "workspace_volume_id",
            "path",
            name=op.f("uq_volume_objects_workspace_volume_path"),
            postgresql_include=[],
            postgresql_nulls_not_distinct=False,
        ),
    )
    op.create_index(
        op.f("ix_volume_objects_workspace_volume_modified"),
        "volume_objects",
        ["workspace_volume_id", "modified_at"],
        unique=False,
    )
    op.create_index(op.f("ix_volume_objects_workspace_path"), "volume_objects", ["workspace_id", "path"], unique=False)
