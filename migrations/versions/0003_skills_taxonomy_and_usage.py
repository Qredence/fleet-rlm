"""Add tenant-scoped skills taxonomy, versioning, and run usage tables.

Revision ID: 0003_skills_taxonomy_and_usage
Revises: 0002_tenant_fk_hardening
Create Date: 2026-02-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "0003_skills_taxonomy_and_usage"
down_revision = "0002_tenant_fk_hardening"
branch_labels = None
depends_on = None


skill_source = postgresql.ENUM(
    "scaffold",
    "imported",
    "user_defined",
    "system",
    name="skill_source",
    create_type=False,
)
skill_status = postgresql.ENUM(
    "active",
    "deprecated",
    "disabled",
    name="skill_status",
    create_type=False,
)
skill_link_source = postgresql.ENUM(
    "manual",
    "inferred",
    "imported",
    name="skill_link_source",
    create_type=False,
)
skill_usage_status = postgresql.ENUM(
    "started",
    "completed",
    "failed",
    "cancelled",
    name="skill_usage_status",
    create_type=False,
)

ENUMS = [
    skill_source,
    skill_status,
    skill_link_source,
    skill_usage_status,
]

RLS_TABLES = [
    "skill_taxonomies",
    "taxonomy_terms",
    "skills",
    "skill_versions",
    "skill_term_links",
    "run_skill_usages",
]


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
    bind = op.get_bind()
    for enum_type in ENUMS:
        enum_type.create(bind, checkfirst=True)

    op.create_table(
        "skill_taxonomies",
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
        sa.Column("key", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL",
            name="fk_skill_taxonomies_tenant_created_by_user__users_tenant_id_id",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_skill_taxonomies_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_skill_taxonomies_tenant_key"),
    )

    op.create_table(
        "taxonomy_terms",
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
        sa.Column("taxonomy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_term_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "synonyms",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
        ),
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "taxonomy_id"],
            ["skill_taxonomies.tenant_id", "skill_taxonomies.id"],
            ondelete="CASCADE",
            name="fk_tax_terms_tenant_taxonomy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_term_id"],
            ["taxonomy_terms.tenant_id", "taxonomy_terms.id"],
            ondelete="SET NULL",
            name="fk_taxonomy_terms_tenant_parent__taxonomy_terms_tenant_id_id",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_taxonomy_terms_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "taxonomy_id",
            "slug",
            name="uq_taxonomy_terms_tenant_taxonomy_slug",
        ),
        sa.CheckConstraint(
            "id <> parent_term_id", name="ck_taxonomy_terms_parent_not_self"
        ),
    )

    op.create_table(
        "skills",
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
        sa.Column("stable_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "source", skill_source, nullable=False, server_default=sa.text("'scaffold'")
        ),
        sa.Column(
            "status", skill_status, nullable=False, server_default=sa.text("'active'")
        ),
        sa.Column("latest_version", sa.Integer(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL",
            name="fk_skills_tenant_created_by_user__users_tenant_id_id",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_skills_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id", "stable_key", name="uq_skills_tenant_stable_key"
        ),
    )

    op.create_table(
        "skill_versions",
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
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_num", sa.Integer(), nullable=False),
        sa.Column("semver", sa.String(length=64), nullable=True),
        sa.Column(
            "manifest_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("checksum", sa.String(length=255), nullable=True),
        sa.Column("source_uri", sa.Text(), nullable=True),
        sa.Column(
            "is_current", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "skill_id"],
            ["skills.tenant_id", "skills.id"],
            ondelete="CASCADE",
            name="fk_skill_versions_tenant_skill__skills_tenant_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL",
            name="fk_skill_versions_tenant_created_by_user__users_tenant_id_id",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_skill_versions_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "skill_id",
            "version_num",
            name="uq_skill_versions_tenant_skill_version_num",
        ),
        sa.CheckConstraint(
            "version_num > 0", name="ck_skill_versions_version_positive"
        ),
    )

    op.create_table(
        "skill_term_links",
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
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("term_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "confidence",
            sa.Numeric(5, 4),
            nullable=False,
            server_default=sa.text("1.0"),
        ),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "source",
            skill_link_source,
            nullable=False,
            server_default=sa.text("'manual'"),
        ),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "skill_id"],
            ["skills.tenant_id", "skills.id"],
            ondelete="CASCADE",
            name="fk_skill_term_links_tenant_skill__skills_tenant_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "term_id"],
            ["taxonomy_terms.tenant_id", "taxonomy_terms.id"],
            ondelete="CASCADE",
            name="fk_skill_term_links_tenant_term__taxonomy_terms_tenant_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["users.tenant_id", "users.id"],
            ondelete="SET NULL",
            name="fk_skill_term_links_tenant_created_by_user__users_tenant_id_id",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_skill_term_links_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "skill_id",
            "term_id",
            name="uq_skill_term_links_tenant_skill_term",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_skill_term_links_confidence_range",
        ),
    )

    op.create_table(
        "run_skill_usages",
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
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("step_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("skill_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("skill_version_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "status",
            skill_usage_status,
            nullable=False,
            server_default=sa.text("'started'"),
        ),
        sa.Column("invocation_name", sa.String(length=255), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "run_id"],
            ["runs.tenant_id", "runs.id"],
            ondelete="CASCADE",
            name="fk_run_skill_usages_tenant_run__runs_tenant_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "step_id"],
            ["run_steps.tenant_id", "run_steps.id"],
            ondelete="SET NULL",
            name="fk_run_skill_usages_tenant_step__run_steps_tenant_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "skill_id"],
            ["skills.tenant_id", "skills.id"],
            ondelete="RESTRICT",
            name="fk_run_skill_usages_tenant_skill__skills_tenant_id_id",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "skill_version_id"],
            ["skill_versions.tenant_id", "skill_versions.id"],
            ondelete="SET NULL",
            name="fk_run_skill_usages_tenant_skill_ver",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_run_skill_usages_tenant_id_id"),
        sa.CheckConstraint(
            "completed_at IS NULL OR completed_at >= started_at",
            name="ck_run_skill_usages_time_order",
        ),
    )

    op.create_index(
        "ix_skill_taxonomies_tenant_created_at",
        "skill_taxonomies",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_skill_taxonomies_tenant_key", "skill_taxonomies", ["tenant_id", "key"]
    )

    op.create_index(
        "ix_taxonomy_terms_tenant_taxonomy_parent_sort",
        "taxonomy_terms",
        ["tenant_id", "taxonomy_id", "parent_term_id", "sort_order"],
    )
    op.create_index(
        "ix_taxonomy_terms_tenant_slug", "taxonomy_terms", ["tenant_id", "slug"]
    )
    op.create_index(
        "ix_taxonomy_terms_synonyms",
        "taxonomy_terms",
        ["synonyms"],
        postgresql_using="gin",
    )

    op.create_index(
        "ix_skills_tenant_created_at", "skills", ["tenant_id", "created_at"]
    )
    op.create_index("ix_skills_tenant_status", "skills", ["tenant_id", "status"])
    op.create_index(
        "ix_skills_tenant_display_name", "skills", ["tenant_id", "display_name"]
    )

    op.create_index(
        "ix_skill_versions_tenant_skill_version_num",
        "skill_versions",
        ["tenant_id", "skill_id", "version_num"],
    )
    op.create_index(
        "uq_skill_versions_tenant_skill_current",
        "skill_versions",
        ["tenant_id", "skill_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )

    op.create_index(
        "ix_skill_term_links_tenant_skill",
        "skill_term_links",
        ["tenant_id", "skill_id"],
    )
    op.create_index(
        "ix_skill_term_links_tenant_term", "skill_term_links", ["tenant_id", "term_id"]
    )

    op.create_index(
        "ix_run_skill_usages_tenant_run_started_at",
        "run_skill_usages",
        ["tenant_id", "run_id", "started_at"],
    )
    op.create_index(
        "ix_run_skill_usages_tenant_skill_started_at",
        "run_skill_usages",
        ["tenant_id", "skill_id", "started_at"],
    )
    op.create_index(
        "ix_run_skill_usages_tenant_step", "run_skill_usages", ["tenant_id", "step_id"]
    )

    for table_name in RLS_TABLES:
        _enable_rls(table_name)


def downgrade() -> None:
    for table_name in reversed(RLS_TABLES):
        op.execute(
            f"DROP POLICY IF EXISTS tenant_isolation_{table_name} ON {table_name}"
        )

    op.drop_index("ix_run_skill_usages_tenant_step", table_name="run_skill_usages")
    op.drop_index(
        "ix_run_skill_usages_tenant_skill_started_at",
        table_name="run_skill_usages",
    )
    op.drop_index(
        "ix_run_skill_usages_tenant_run_started_at",
        table_name="run_skill_usages",
    )

    op.drop_index("ix_skill_term_links_tenant_term", table_name="skill_term_links")
    op.drop_index("ix_skill_term_links_tenant_skill", table_name="skill_term_links")

    op.drop_index(
        "uq_skill_versions_tenant_skill_current",
        table_name="skill_versions",
    )
    op.drop_index(
        "ix_skill_versions_tenant_skill_version_num",
        table_name="skill_versions",
    )

    op.drop_index("ix_skills_tenant_display_name", table_name="skills")
    op.drop_index("ix_skills_tenant_status", table_name="skills")
    op.drop_index("ix_skills_tenant_created_at", table_name="skills")

    op.drop_index("ix_taxonomy_terms_synonyms", table_name="taxonomy_terms")
    op.drop_index("ix_taxonomy_terms_tenant_slug", table_name="taxonomy_terms")
    op.drop_index(
        "ix_taxonomy_terms_tenant_taxonomy_parent_sort",
        table_name="taxonomy_terms",
    )

    op.drop_index("ix_skill_taxonomies_tenant_key", table_name="skill_taxonomies")
    op.drop_index(
        "ix_skill_taxonomies_tenant_created_at",
        table_name="skill_taxonomies",
    )

    op.drop_table("run_skill_usages")
    op.drop_table("skill_term_links")
    op.drop_table("skill_versions")
    op.drop_table("skills")
    op.drop_table("taxonomy_terms")
    op.drop_table("skill_taxonomies")

    bind = op.get_bind()
    for enum_type in reversed(ENUMS):
        enum_type.drop(bind, checkfirst=True)
