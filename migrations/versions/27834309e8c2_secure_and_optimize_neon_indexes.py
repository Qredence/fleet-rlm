"""secure_and_optimize_neon_indexes

Revision ID: 27834309e8c2
Revises: c4e8f1a92b10
Create Date: 2026-06-20 07:14:02.532308
"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "27834309e8c2"
down_revision = "c4e8f1a92b10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Consolidate Duplicate Indexes: Remove redundant unique index on neon_auth.organization
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    if is_postgres:
        # Drop redundant uidx on neon_auth.organization.slug (duplicates organization_slug_key)
        op.execute(
            """
            DO $$
            BEGIN
              IF to_regclass('neon_auth.organization_slug_uidx') IS NOT NULL THEN
                DROP INDEX neon_auth.organization_slug_uidx;
              END IF;
            END;
            $$;
            """
        )

    # 2. Add Key Foreign Key Indexes: Create missing covering indexes on public tables
    op.create_index("ix_tenant_memberships_user_id", "tenant_memberships", ["user_id"])
    op.create_index("ix_chat_sessions_user_id", "chat_sessions", ["user_id"])
    op.create_index("ix_workspace_memberships_user_id", "workspace_memberships", ["user_id"])
    op.create_index("ix_workspace_memberships_tenant_user", "workspace_memberships", ["tenant_id", "user_id"])
    op.create_index("ix_chat_turns_user_id", "chat_turns", ["user_id"])
    op.create_index(
        "ix_chat_turns_tenant_workspace_session",
        "chat_turns",
        ["tenant_id", "workspace_id", "session_id"],
    )
    op.create_index("ix_workspaces_created_by_user_id", "workspaces", ["created_by_user_id"])

    op.create_index("ix_execution_runs_created_by_user_id", "execution_runs", ["created_by_user_id"])
    op.create_index("ix_execution_runs_session_id", "execution_runs", ["session_id"])
    op.create_index("ix_execution_runs_turn_id", "execution_runs", ["turn_id"])
    op.create_index("ix_execution_runs_sandbox_session_id", "execution_runs", ["sandbox_session_id"])
    op.create_index("ix_execution_runs_parent_run_id", "execution_runs", ["parent_run_id"])

    op.create_index("ix_sandbox_sessions_created_by_user_id", "sandbox_sessions", ["created_by_user_id"])
    op.create_index(
        "ix_workspace_runtime_settings_updated_by_user_id", "workspace_runtime_settings", ["updated_by_user_id"]
    )

    op.create_index("ix_jobs_tenant_id", "jobs", ["tenant_id"])
    op.create_index("ix_jobs_tenant_workspace", "jobs", ["tenant_id", "workspace_id"])

    op.create_index("ix_llm_role_bindings_profile_id", "llm_role_bindings", ["profile_id"])

    op.create_index("ix_memory_items_tenant_id", "memory_items", ["tenant_id"])
    op.create_index("ix_memory_items_tenant_workspace", "memory_items", ["tenant_id", "workspace_id"])
    op.create_index("ix_memory_items_user_id", "memory_items", ["user_id"])

    op.create_index("ix_memory_links_tenant_id", "memory_links", ["tenant_id"])
    op.create_index("ix_memory_links_tenant_workspace", "memory_links", ["tenant_id", "workspace_id"])

    op.create_index("ix_optimization_modules_tenant_id", "optimization_modules", ["tenant_id"])
    op.create_index("ix_optimization_modules_tenant_workspace", "optimization_modules", ["tenant_id", "workspace_id"])

    op.create_index("ix_datasets_created_by_user_id", "datasets", ["created_by_user_id"])
    op.create_index("ix_datasets_optimization_module_id", "datasets", ["optimization_module_id"])
    op.create_index("ix_datasets_tenant_workspace", "datasets", ["tenant_id", "workspace_id"])

    op.create_index("ix_dataset_examples_tenant_workspace", "dataset_examples", ["tenant_id", "workspace_id"])

    op.create_index("ix_optimization_runs_created_by_user_id", "optimization_runs", ["created_by_user_id"])
    op.create_index("ix_optimization_runs_optimization_module_id", "optimization_runs", ["optimization_module_id"])
    op.create_index("ix_optimization_runs_dataset_id", "optimization_runs", ["dataset_id"])
    op.create_index("ix_optimization_runs_tenant_id", "optimization_runs", ["tenant_id"])
    op.create_index("ix_optimization_runs_tenant_workspace", "optimization_runs", ["tenant_id", "workspace_id"])

    op.create_index("ix_evaluation_results_dataset_example_id", "evaluation_results", ["dataset_example_id"])
    op.create_index("ix_evaluation_results_tenant_workspace", "evaluation_results", ["tenant_id", "workspace_id"])
    op.create_index("ix_trace_feedback_reviewer_user_id", "trace_feedback", ["reviewer_user_id"])
    op.create_index("ix_trace_feedback_tenant_id", "trace_feedback", ["tenant_id"])
    op.create_index("ix_trace_feedback_tenant_workspace", "trace_feedback", ["tenant_id", "workspace_id"])

    op.create_index("ix_execution_steps_session_id", "execution_steps", ["session_id"])
    op.create_index(
        "ix_execution_steps_tenant_workspace_run", "execution_steps", ["tenant_id", "workspace_id", "run_id"]
    )
    op.create_index("ix_execution_steps_turn_id", "execution_steps", ["turn_id"])

    op.create_index("ix_execution_events_session_id", "execution_events", ["session_id"])
    op.create_index(
        "ix_execution_events_tenant_workspace_run",
        "execution_events",
        ["tenant_id", "workspace_id", "run_id"],
    )
    op.create_index("ix_execution_events_turn_id", "execution_events", ["turn_id"])

    op.create_index("ix_session_state_snapshots_tenant_id", "session_state_snapshots", ["tenant_id"])
    op.create_index(
        "ix_session_state_snapshots_tenant_workspace_session",
        "session_state_snapshots",
        ["tenant_id", "workspace_id", "session_id"],
    )

    op.create_index("ix_artifacts_tenant_workspace", "artifacts", ["tenant_id", "workspace_id"])
    op.create_index("ix_artifacts_run_id", "artifacts", ["run_id"])
    op.create_index("ix_artifacts_step_id", "artifacts", ["step_id"])
    op.create_index("ix_artifacts_event_id", "artifacts", ["event_id"])
    op.create_index("ix_artifacts_session_id", "artifacts", ["session_id"])
    op.create_index("ix_artifacts_turn_id", "artifacts", ["turn_id"])

    op.create_index("ix_program_versions_source_run_id", "program_versions", ["source_run_id"])
    op.create_index("ix_program_versions_tenant_workspace", "program_versions", ["tenant_id", "workspace_id"])

    op.create_index("ix_prompt_snapshots_tenant_id", "prompt_snapshots", ["tenant_id"])
    op.create_index("ix_prompt_snapshots_tenant_workspace", "prompt_snapshots", ["tenant_id", "workspace_id"])

    op.create_index("ix_external_traces_run_id", "external_traces", ["run_id"])
    op.create_index("ix_external_traces_session_id", "external_traces", ["session_id"])
    op.create_index("ix_external_traces_tenant_workspace", "external_traces", ["tenant_id", "workspace_id"])
    op.create_index("ix_external_traces_turn_id", "external_traces", ["turn_id"])


def downgrade() -> None:
    # 1. Drop public indexes
    op.drop_index("ix_external_traces_turn_id", table_name="external_traces")
    op.drop_index("ix_external_traces_tenant_workspace", table_name="external_traces")
    op.drop_index("ix_external_traces_session_id", table_name="external_traces")
    op.drop_index("ix_external_traces_run_id", table_name="external_traces")

    op.drop_index("ix_prompt_snapshots_tenant_workspace", table_name="prompt_snapshots")
    op.drop_index("ix_prompt_snapshots_tenant_id", table_name="prompt_snapshots")

    op.drop_index("ix_program_versions_tenant_workspace", table_name="program_versions")
    op.drop_index("ix_program_versions_source_run_id", table_name="program_versions")

    op.drop_index("ix_artifacts_turn_id", table_name="artifacts")
    op.drop_index("ix_artifacts_session_id", table_name="artifacts")
    op.drop_index("ix_artifacts_event_id", table_name="artifacts")
    op.drop_index("ix_artifacts_step_id", table_name="artifacts")
    op.drop_index("ix_artifacts_run_id", table_name="artifacts")
    op.drop_index("ix_artifacts_tenant_workspace", table_name="artifacts")

    op.drop_index("ix_session_state_snapshots_tenant_workspace_session", table_name="session_state_snapshots")
    op.drop_index("ix_session_state_snapshots_tenant_id", table_name="session_state_snapshots")

    op.drop_index("ix_execution_events_turn_id", table_name="execution_events")
    op.drop_index("ix_execution_events_tenant_workspace_run", table_name="execution_events")
    op.drop_index("ix_execution_events_session_id", table_name="execution_events")

    op.drop_index("ix_execution_steps_turn_id", table_name="execution_steps")
    op.drop_index("ix_execution_steps_tenant_workspace_run", table_name="execution_steps")
    op.drop_index("ix_execution_steps_session_id", table_name="execution_steps")

    op.drop_index("ix_trace_feedback_tenant_workspace", table_name="trace_feedback")
    op.drop_index("ix_trace_feedback_tenant_id", table_name="trace_feedback")
    op.drop_index("ix_trace_feedback_reviewer_user_id", table_name="trace_feedback")
    op.drop_index("ix_evaluation_results_tenant_workspace", table_name="evaluation_results")
    op.drop_index("ix_evaluation_results_dataset_example_id", table_name="evaluation_results")

    op.drop_index("ix_optimization_runs_tenant_workspace", table_name="optimization_runs")
    op.drop_index("ix_optimization_runs_tenant_id", table_name="optimization_runs")
    op.drop_index("ix_optimization_runs_dataset_id", table_name="optimization_runs")
    op.drop_index("ix_optimization_runs_optimization_module_id", table_name="optimization_runs")
    op.drop_index("ix_optimization_runs_created_by_user_id", table_name="optimization_runs")

    op.drop_index("ix_dataset_examples_tenant_workspace", table_name="dataset_examples")
    op.drop_index("ix_datasets_tenant_workspace", table_name="datasets")
    op.drop_index("ix_datasets_optimization_module_id", table_name="datasets")
    op.drop_index("ix_datasets_created_by_user_id", table_name="datasets")

    op.drop_index("ix_optimization_modules_tenant_workspace", table_name="optimization_modules")
    op.drop_index("ix_optimization_modules_tenant_id", table_name="optimization_modules")

    op.drop_index("ix_memory_links_tenant_workspace", table_name="memory_links")
    op.drop_index("ix_memory_links_tenant_id", table_name="memory_links")

    op.drop_index("ix_memory_items_user_id", table_name="memory_items")
    op.drop_index("ix_memory_items_tenant_workspace", table_name="memory_items")
    op.drop_index("ix_memory_items_tenant_id", table_name="memory_items")

    op.drop_index("ix_llm_role_bindings_profile_id", table_name="llm_role_bindings")

    op.drop_index("ix_jobs_tenant_workspace", table_name="jobs")
    op.drop_index("ix_jobs_tenant_id", table_name="jobs")

    op.drop_index("ix_workspace_runtime_settings_updated_by_user_id", table_name="workspace_runtime_settings")
    op.drop_index("ix_sandbox_sessions_created_by_user_id", table_name="sandbox_sessions")

    op.drop_index("ix_execution_runs_parent_run_id", table_name="execution_runs")
    op.drop_index("ix_execution_runs_sandbox_session_id", table_name="execution_runs")
    op.drop_index("ix_execution_runs_turn_id", table_name="execution_runs")
    op.drop_index("ix_execution_runs_session_id", table_name="execution_runs")
    op.drop_index("ix_execution_runs_created_by_user_id", table_name="execution_runs")

    op.drop_index("ix_workspaces_created_by_user_id", table_name="workspaces")
    op.drop_index("ix_chat_turns_tenant_workspace_session", table_name="chat_turns")
    op.drop_index("ix_chat_turns_user_id", table_name="chat_turns")
    op.drop_index("ix_workspace_memberships_tenant_user", table_name="workspace_memberships")
    op.drop_index("ix_workspace_memberships_user_id", table_name="workspace_memberships")
    op.drop_index("ix_chat_sessions_user_id", table_name="chat_sessions")
    op.drop_index("ix_tenant_memberships_user_id", table_name="tenant_memberships")

    # 2. Re-create organization_slug_uidx if postgres
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"
    if is_postgres:
        op.execute(
            """
            DO $$
            BEGIN
              IF to_regclass('neon_auth.organization') IS NOT NULL
                 AND to_regclass('neon_auth.organization_slug_uidx') IS NULL THEN
                CREATE UNIQUE INDEX organization_slug_uidx ON neon_auth.organization (slug);
              END IF;
            END;
            $$;
            """
        )
