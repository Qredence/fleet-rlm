"""scope_workspace_runtime_settings_uq_to_tenant

Tighten the workspace_runtime_settings uniqueness constraint from
workspace_id-only to (tenant_id, workspace_id). Defense-in-depth alongside
the composite FK to workspaces(tenant_id, id): ensures any future RLS gap
or caller bug cannot silently overwrite a different tenant's workspace row.

Revision ID: h4d5e6f7g8h9
Revises: g3c4d5e6f7g8
Create Date: 2026-06-25 18:00:00.000000
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "h4d5e6f7g8h9"
down_revision = "g3c4d5e6f7g8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "uq_workspace_runtime_settings_workspace_id",
        "workspace_runtime_settings",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_workspace_runtime_settings_tenant_workspace",
        "workspace_runtime_settings",
        ["tenant_id", "workspace_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_workspace_runtime_settings_tenant_workspace",
        "workspace_runtime_settings",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_workspace_runtime_settings_workspace_id",
        "workspace_runtime_settings",
        ["workspace_id"],
    )
