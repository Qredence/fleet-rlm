"""Rebuild schema to the Fleet-RLM target Postgres baseline.

Revision ID: 0010_target_postgres_schema
Revises: 0009_remove_modal_runtime_surface
Create Date: 2026-04-18
"""

from __future__ import annotations

from collections.abc import Iterable

from alembic import op
from sqlalchemy import text

# revision identifiers, used by Alembic.
revision = "0010_target_postgres_schema"
down_revision = "0009_remove_modal_runtime_surface"
branch_labels = None
depends_on = None


_TENANT_ONLY_RLS_TABLES = [
    "users",
    "tenant_memberships",
    "workspaces",
    "workspace_memberships",
    "workspace_runtime_settings",
    "tenant_subscriptions",
]

_WORKSPACE_RLS_TABLES = [
    "chat_sessions",
    "chat_turns",
    "execution_runs",
    "execution_steps",
    "execution_events",
    "session_state_snapshots",
    "sandbox_sessions",
    "workspace_volumes",
    "volume_objects",
    "artifacts",
    "memory_items",
    "memory_links",
    "optimization_modules",
    "datasets",
    "dataset_examples",
    "optimization_runs",
    "evaluation_results",
    "prompt_snapshots",
    "program_versions",
    "external_traces",
    "trace_feedback",
    "jobs",
    "outbox_events",
]

_BASELINE_ENUM_SQL = [
    "CREATE TYPE tenant_plan AS ENUM ('free', 'team', 'enterprise');",
    "CREATE TYPE tenant_status AS ENUM ('active', 'suspended', 'deleted');",
    "CREATE TYPE billing_source AS ENUM ('azure_marketplace', 'manual');",
    "CREATE TYPE subscription_status AS ENUM ('trial', 'active', 'past_due', 'cancelled', 'expired');",
    "CREATE TYPE membership_role AS ENUM ('owner', 'admin', 'member', 'viewer');",
    "CREATE TYPE workspace_status AS ENUM ('active', 'archived', 'deleted');",
    "CREATE TYPE chat_session_status AS ENUM ('active', 'archived', 'failed');",
    "CREATE TYPE job_type AS ENUM ('run_task', 'memory_compaction', 'evaluation', 'maintenance', 'optimization', 'session_export', 'trace_sync');",
    "CREATE TYPE job_status AS ENUM ('queued', 'leased', 'running', 'succeeded', 'failed', 'dead');",
    "CREATE TYPE memory_scope AS ENUM ('user', 'tenant', 'workspace', 'run', 'session', 'agent');",
    "CREATE TYPE memory_kind AS ENUM ('note', 'summary', 'fact', 'preference', 'context');",
    "CREATE TYPE memory_status AS ENUM ('active', 'superseded', 'deleted');",
    "CREATE TYPE memory_source AS ENUM ('user_input', 'system', 'tool', 'llm', 'imported');",
    "CREATE TYPE outbox_status AS ENUM ('pending', 'dispatched', 'failed');",
    "CREATE TYPE sandbox_provider AS ENUM ('daytona', 'aca_jobs', 'local');",
    "CREATE TYPE sandbox_session_status AS ENUM ('active', 'ended', 'failed');",
    "CREATE TYPE workspace_role AS ENUM ('owner', 'admin', 'member', 'viewer');",
    "CREATE TYPE workspace_volume_status AS ENUM ('provisioning', 'ready', 'error', 'archived');",
    "CREATE TYPE chat_turn_status AS ENUM ('completed', 'cancelled', 'failed', 'degraded');",
    "CREATE TYPE dataset_format AS ENUM ('json', 'jsonl', 'transcript');",
    "CREATE TYPE dataset_source AS ENUM ('upload', 'transcript', 'imported', 'mlflow');",
    "CREATE TYPE volume_object_type AS ENUM ('file', 'directory');",
    "CREATE TYPE run_type AS ENUM ('chat_turn', 'background', 'optimization', 'system');",
    "CREATE TYPE run_status AS ENUM ('queued', 'running', 'completed', 'failed', 'cancelled');",
    "CREATE TYPE optimization_run_status AS ENUM ('running', 'completed', 'failed', 'cancelled');",
    "CREATE TYPE run_step_type AS ENUM ('tool_call', 'repl_exec', 'llm_call', 'retrieval', 'guardrail', 'summary', 'memory', 'output', 'status');",
    "CREATE TYPE external_trace_provider AS ENUM ('mlflow');",
    "CREATE TYPE prompt_snapshot_type AS ENUM ('before', 'after');",
    "CREATE TYPE artifact_kind AS ENUM ('file', 'log', 'report', 'trace', 'image', 'data', 'dataset', 'manifest');",
    "CREATE TYPE artifact_provider AS ENUM ('daytona', 'local', 'memory', 'external');",
]

_BASELINE_TABLE_SQL = [
    "CREATE TABLE tenants (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\tentra_tenant_id VARCHAR(128) NOT NULL, \n\tslug VARCHAR(128), \n\tdisplay_name VARCHAR(255), \n\tdomain VARCHAR(255), \n\tplan tenant_plan DEFAULT 'free' NOT NULL, \n\tstatus tenant_status DEFAULT 'active' NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_tenants_slug UNIQUE (slug), \n\tUNIQUE (entra_tenant_id)\n);",
    "CREATE TABLE tenant_subscriptions (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tbilling_source billing_source DEFAULT 'manual' NOT NULL, \n\tpurchaser_tenant_id VARCHAR(128), \n\tsubscription_id VARCHAR(255) NOT NULL, \n\toffer_id VARCHAR(255), \n\tplan_id VARCHAR(255), \n\tstatus subscription_status DEFAULT 'active' NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tended_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_tenant_subscriptions_source_subscription UNIQUE (tenant_id, billing_source, subscription_id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE users (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tentra_user_id VARCHAR(128) NOT NULL, \n\temail VARCHAR(320), \n\tfull_name VARCHAR(255), \n\tis_active BOOLEAN DEFAULT true NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT uq_users_tenant_entra_user UNIQUE (tenant_id, entra_user_id), \n\tCONSTRAINT uq_users_tenant_id_id UNIQUE (tenant_id, id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE tenant_memberships (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\trole membership_role DEFAULT 'member' NOT NULL, \n\tis_default BOOLEAN DEFAULT false NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_tenant_memberships_tenant_user__users_tenant_id_id FOREIGN KEY(tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT uq_tenant_memberships_tenant_user UNIQUE (tenant_id, user_id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE workspaces (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tslug VARCHAR(128) NOT NULL, \n\tdisplay_name VARCHAR(255), \n\tstatus workspace_status DEFAULT 'active' NOT NULL, \n\tcreated_by_user_id UUID, \n\truntime_provider VARCHAR(32) DEFAULT 'daytona' NOT NULL, \n\tactive_volume_name VARCHAR(255), \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tlast_activity_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_workspaces_created_by_user_id__users_id FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tCONSTRAINT uq_workspaces_tenant_slug UNIQUE (tenant_id, slug), \n\tCONSTRAINT uq_workspaces_tenant_id_id UNIQUE (tenant_id, id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE chat_sessions (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tuser_id UUID, \n\ttitle VARCHAR(255) DEFAULT 'New Session' NOT NULL, \n\tstatus chat_session_status DEFAULT 'active' NOT NULL, \n\tmodel_provider VARCHAR(128), \n\tmodel_name VARCHAR(255), \n\tactive_manifest_path TEXT, \n\tmonotonic_turn_counter INTEGER DEFAULT 0 NOT NULL, \n\trevision BIGINT DEFAULT 0 NOT NULL, \n\tlatest_summary TEXT, \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tlast_activity_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_chat_sessions_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_chat_sessions_user_id__users_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tCONSTRAINT uq_chat_sessions_tenant_workspace_id UNIQUE (tenant_id, workspace_id, id), \n\tCONSTRAINT uq_chat_sessions_tenant_id_id UNIQUE (tenant_id, id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE jobs (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tjob_type job_type NOT NULL, \n\tstatus job_status DEFAULT 'queued' NOT NULL, \n\tpayload JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tattempts INTEGER DEFAULT 0 NOT NULL, \n\tmax_attempts INTEGER DEFAULT 5 NOT NULL, \n\tavailable_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tlocked_at TIMESTAMP WITH TIME ZONE, \n\tlocked_by VARCHAR(255), \n\tidempotency_key VARCHAR(255) NOT NULL, \n\tlast_error JSONB, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_jobs_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT uq_jobs_workspace_idempotency_key UNIQUE (workspace_id, idempotency_key), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE memory_items (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tuser_id UUID, \n\trun_id UUID, \n\tsession_id UUID, \n\tscope memory_scope NOT NULL, \n\tscope_id VARCHAR(255) NOT NULL, \n\tkind memory_kind NOT NULL, \n\tstatus memory_status DEFAULT 'active' NOT NULL, \n\turi TEXT, \n\tcontent_text TEXT, \n\tcontent_json JSONB, \n\tprovenance_json JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tsource memory_source NOT NULL, \n\timportance SMALLINT DEFAULT 0 NOT NULL, \n\ttags TEXT[] DEFAULT '{}'::text[] NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_memory_items_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_memory_items_user_id__users_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tCONSTRAINT ck_memory_items_importance_range CHECK (importance >= 0 AND importance <= 100), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE optimization_modules (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tslug VARCHAR(128) NOT NULL, \n\tdisplay_name VARCHAR(255), \n\tdescription TEXT, \n\trequired_dataset_keys JSONB DEFAULT '[]'::jsonb NOT NULL, \n\toutput_key VARCHAR(128) DEFAULT 'assistant_response' NOT NULL, \n\tstatus VARCHAR(32) DEFAULT 'active' NOT NULL, \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_opt_modules_tenant_workspace FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT uq_optimization_modules_workspace_slug UNIQUE (workspace_id, slug), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE outbox_events (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\taggregate_type VARCHAR(128) NOT NULL, \n\taggregate_id UUID, \n\tevent_type VARCHAR(128) NOT NULL, \n\tpayload_json JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tstatus outbox_status DEFAULT 'pending' NOT NULL, \n\tavailable_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tattempts INTEGER DEFAULT 0 NOT NULL, \n\tlast_error JSONB, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_outbox_events_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE sandbox_sessions (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tcreated_by_user_id UUID, \n\tprovider sandbox_provider NOT NULL, \n\texternal_id VARCHAR(255) NOT NULL, \n\tstatus sandbox_session_status DEFAULT 'active' NOT NULL, \n\tvolume_name VARCHAR(255), \n\tvolume_id VARCHAR(255), \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tdiagnostics_json JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tended_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_sandbox_sessions_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_sandbox_sessions_created_by_user_id__users_id FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tCONSTRAINT uq_sandbox_sessions_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT uq_sandbox_sessions_tenant_workspace_id UNIQUE (tenant_id, workspace_id, id), \n\tCONSTRAINT uq_sandbox_sessions_workspace_provider_external UNIQUE (workspace_id, provider, external_id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE workspace_memberships (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tuser_id UUID NOT NULL, \n\trole workspace_role DEFAULT 'member' NOT NULL, \n\tis_default BOOLEAN DEFAULT false NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_ws_memberships_tenant_workspace FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_workspace_memberships_tenant_user__users_tenant_id_id FOREIGN KEY(tenant_id, user_id) REFERENCES users (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT uq_workspace_memberships_workspace_user UNIQUE (workspace_id, user_id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE workspace_runtime_settings (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tupdated_by_user_id UUID, \n\tsettings_json JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_ws_runtime_settings_tenant_workspace FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_workspace_runtime_settings_updated_by_user_id__users_id FOREIGN KEY(updated_by_user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tCONSTRAINT uq_workspace_runtime_settings_workspace_id UNIQUE (workspace_id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE workspace_volumes (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tprovider sandbox_provider NOT NULL, \n\texternal_volume_id VARCHAR(255), \n\texternal_volume_name VARCHAR(255), \n\tmount_path TEXT, \n\troot_path TEXT, \n\tstatus workspace_volume_status DEFAULT 'provisioning' NOT NULL, \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_workspace_volumes_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT uq_workspace_volumes_workspace_external_id UNIQUE (workspace_id, external_volume_id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE chat_turns (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tsession_id UUID NOT NULL, \n\tuser_id UUID, \n\trun_id UUID, \n\tturn_index INTEGER NOT NULL, \n\tuser_message TEXT NOT NULL, \n\tassistant_message TEXT, \n\tstatus chat_turn_status DEFAULT 'completed' NOT NULL, \n\tdegraded BOOLEAN DEFAULT false NOT NULL, \n\tmodel_provider VARCHAR(128), \n\tmodel_name VARCHAR(255), \n\ttokens_in INTEGER, \n\ttokens_out INTEGER, \n\tlatency_ms INTEGER, \n\terror_json JSONB, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_chat_turns_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_chat_turns_tenant_workspace_session__chat_sessions FOREIGN KEY(tenant_id, workspace_id, session_id) REFERENCES chat_sessions (tenant_id, workspace_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_chat_turns_user_id__users_id FOREIGN KEY(user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tCONSTRAINT uq_chat_turns_tenant_workspace_id UNIQUE (tenant_id, workspace_id, id), \n\tCONSTRAINT uq_chat_turns_session_turn_index UNIQUE (session_id, turn_index), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE datasets (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\toptimization_module_id UUID, \n\tcreated_by_user_id UUID, \n\tname VARCHAR(255) NOT NULL, \n\trow_count INTEGER DEFAULT 0 NOT NULL, \n\tformat dataset_format NOT NULL, \n\tsource dataset_source DEFAULT 'upload' NOT NULL, \n\turi TEXT, \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_datasets_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_datasets_optimization_module_id__optimization_modules_id FOREIGN KEY(optimization_module_id) REFERENCES optimization_modules (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_datasets_created_by_user_id__users_id FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE memory_links (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tsource_memory_id UUID NOT NULL, \n\ttarget_kind VARCHAR(64) NOT NULL, \n\ttarget_id UUID NOT NULL, \n\tlink_type VARCHAR(128) NOT NULL, \n\tweight FLOAT DEFAULT 1.0 NOT NULL, \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_memory_links_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_memory_links_source_memory_id__memory_items_id FOREIGN KEY(source_memory_id) REFERENCES memory_items (id) ON DELETE CASCADE, \n\tCONSTRAINT uq_memory_links_source_target_type UNIQUE (source_memory_id, target_kind, target_id, link_type), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE session_state_snapshots (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tsession_id UUID NOT NULL, \n\trevision BIGINT NOT NULL, \n\tstate_json JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tmanifest_json JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_by_user_id UUID, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_state_snapshots_tenant_workspace FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_state_snapshots_tenant_workspace_session FOREIGN KEY(tenant_id, workspace_id, session_id) REFERENCES chat_sessions (tenant_id, workspace_id, id) ON DELETE CASCADE, \n\tCONSTRAINT uq_session_state_snapshots_session_revision UNIQUE (session_id, revision), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE volume_objects (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tworkspace_volume_id UUID NOT NULL, \n\tpath TEXT NOT NULL, \n\tobject_type volume_object_type NOT NULL, \n\tmime_type VARCHAR(255), \n\tsize_bytes BIGINT, \n\tchecksum VARCHAR(255), \n\tmodified_at TIMESTAMP WITH TIME ZONE, \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_volume_objects_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_volume_objects_workspace_volume_id__workspace_volumes_id FOREIGN KEY(workspace_volume_id) REFERENCES workspace_volumes (id) ON DELETE CASCADE, \n\tCONSTRAINT uq_volume_objects_workspace_volume_path UNIQUE (workspace_volume_id, path), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE dataset_examples (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tdataset_id UUID NOT NULL, \n\trow_index INTEGER NOT NULL, \n\tinput_json JSONB DEFAULT '{}'::jsonb NOT NULL, \n\texpected_output TEXT, \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_dataset_examples_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_dataset_examples_dataset_id__datasets_id FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE CASCADE, \n\tCONSTRAINT uq_dataset_examples_dataset_row_index UNIQUE (dataset_id, row_index), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE execution_runs (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tsession_id UUID, \n\tturn_id UUID, \n\tparent_run_id UUID, \n\texternal_run_id VARCHAR(255), \n\trun_type run_type DEFAULT 'chat_turn' NOT NULL, \n\tcreated_by_user_id UUID, \n\tstatus run_status DEFAULT 'running' NOT NULL, \n\tmodel_provider VARCHAR(128), \n\tmodel_name VARCHAR(255), \n\tsandbox_provider sandbox_provider, \n\tsandbox_session_id UUID, \n\terror_json JSONB, \n\tmetrics_json JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcompleted_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_execution_runs_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_execution_runs_created_by_user_id__users_id FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_execution_runs_session_id__chat_sessions_id FOREIGN KEY(session_id) REFERENCES chat_sessions (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_execution_runs_turn_id__chat_turns_id FOREIGN KEY(turn_id) REFERENCES chat_turns (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_execution_runs_sandbox_session_id__sandbox_sessions_id FOREIGN KEY(sandbox_session_id) REFERENCES sandbox_sessions (id) ON DELETE SET NULL, \n\tCONSTRAINT uq_execution_runs_tenant_workspace_id UNIQUE (tenant_id, workspace_id, id), \n\tCONSTRAINT uq_execution_runs_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT uq_execution_runs_workspace_external_run UNIQUE (workspace_id, external_run_id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE, \n\tFOREIGN KEY(parent_run_id) REFERENCES execution_runs (id) ON DELETE SET NULL\n);",
    "CREATE TABLE optimization_runs (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\toptimization_module_id UUID, \n\tdataset_id UUID, \n\tcreated_by_user_id UUID, \n\tstatus optimization_run_status DEFAULT 'running' NOT NULL, \n\tprogram_spec VARCHAR(255) NOT NULL, \n\toptimizer VARCHAR(64) NOT NULL, \n\tauto VARCHAR(16), \n\ttrain_ratio FLOAT DEFAULT 0.8 NOT NULL, \n\ttrain_examples INTEGER, \n\tvalidation_examples INTEGER, \n\tvalidation_score FLOAT, \n\toutput_path TEXT, \n\tmanifest_path TEXT, \n\terror TEXT, \n\tphase VARCHAR(64), \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tstarted_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcompleted_at TIMESTAMP WITH TIME ZONE, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_optimization_runs_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_optimization_runs_module_id__optimization_modules_id FOREIGN KEY(optimization_module_id) REFERENCES optimization_modules (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_optimization_runs_dataset_id__datasets_id FOREIGN KEY(dataset_id) REFERENCES datasets (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_optimization_runs_created_by_user_id__users_id FOREIGN KEY(created_by_user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tCONSTRAINT ck_optimization_runs_train_ratio_range CHECK (train_ratio > 0 AND train_ratio < 1), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE evaluation_results (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\toptimization_run_id UUID NOT NULL, \n\tdataset_example_id UUID, \n\texample_index INTEGER NOT NULL, \n\tinput_data JSONB DEFAULT '{}'::jsonb NOT NULL, \n\texpected_output TEXT, \n\tpredicted_output TEXT, \n\tscore FLOAT NOT NULL, \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_evaluation_results_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_evaluation_results_run_id__optimization_runs_id FOREIGN KEY(optimization_run_id) REFERENCES optimization_runs (id) ON DELETE CASCADE, \n\tCONSTRAINT fk_evaluation_results_dataset_example_id__dataset_examples_id FOREIGN KEY(dataset_example_id) REFERENCES dataset_examples (id) ON DELETE SET NULL, \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE execution_events (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tsession_id UUID, \n\tturn_id UUID, \n\tsequence BIGINT NOT NULL, \n\tevent_type VARCHAR(128) NOT NULL, \n\tpayload_json JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_execution_events_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_execution_events_tenant_workspace_run__execution_runs FOREIGN KEY(tenant_id, workspace_id, run_id) REFERENCES execution_runs (tenant_id, workspace_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_execution_events_session_id__chat_sessions_id FOREIGN KEY(session_id) REFERENCES chat_sessions (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_execution_events_turn_id__chat_turns_id FOREIGN KEY(turn_id) REFERENCES chat_turns (id) ON DELETE SET NULL, \n\tCONSTRAINT uq_execution_events_tenant_workspace_id UNIQUE (tenant_id, workspace_id, id), \n\tCONSTRAINT uq_execution_events_run_sequence UNIQUE (run_id, sequence), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE execution_steps (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\trun_id UUID NOT NULL, \n\tsession_id UUID, \n\tturn_id UUID, \n\tstep_index INTEGER NOT NULL, \n\tstep_type run_step_type NOT NULL, \n\ttool_name VARCHAR(255), \n\tinput_json JSONB, \n\toutput_json JSONB, \n\tcost_usd_micros BIGINT, \n\ttokens_in INTEGER, \n\ttokens_out INTEGER, \n\tlatency_ms INTEGER, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_execution_steps_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_execution_steps_tenant_workspace_run__execution_runs FOREIGN KEY(tenant_id, workspace_id, run_id) REFERENCES execution_runs (tenant_id, workspace_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_execution_steps_session_id__chat_sessions_id FOREIGN KEY(session_id) REFERENCES chat_sessions (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_execution_steps_turn_id__chat_turns_id FOREIGN KEY(turn_id) REFERENCES chat_turns (id) ON DELETE SET NULL, \n\tCONSTRAINT uq_execution_steps_tenant_workspace_id UNIQUE (tenant_id, workspace_id, id), \n\tCONSTRAINT uq_execution_steps_run_step_index UNIQUE (run_id, step_index), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE external_traces (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\trun_id UUID, \n\tsession_id UUID, \n\tturn_id UUID, \n\tprovider external_trace_provider DEFAULT 'mlflow' NOT NULL, \n\ttrace_id VARCHAR(255) NOT NULL, \n\tclient_request_id VARCHAR(255), \n\texperiment_id VARCHAR(255), \n\texperiment_name VARCHAR(255), \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tobserved_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_external_traces_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_external_traces_run_id__execution_runs_id FOREIGN KEY(run_id) REFERENCES execution_runs (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_external_traces_session_id__chat_sessions_id FOREIGN KEY(session_id) REFERENCES chat_sessions (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_external_traces_turn_id__chat_turns_id FOREIGN KEY(turn_id) REFERENCES chat_turns (id) ON DELETE SET NULL, \n\tCONSTRAINT uq_external_traces_tenant_provider_trace_id UNIQUE (tenant_id, provider, trace_id), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE program_versions (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tprogram_key VARCHAR(255) NOT NULL, \n\tdisplay_name VARCHAR(255), \n\tkind VARCHAR(64) DEFAULT 'compiled' NOT NULL, \n\tstatus VARCHAR(32) DEFAULT 'active' NOT NULL, \n\tdspy_signature VARCHAR(255), \n\tversion_tag VARCHAR(128), \n\tschema_version INTEGER DEFAULT 1 NOT NULL, \n\tsource_run_id UUID, \n\tcreated_by_user_id UUID, \n\tprogram_json JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tupdated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_program_versions_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_program_versions_source_run_id__execution_runs_id FOREIGN KEY(source_run_id) REFERENCES execution_runs (id) ON DELETE SET NULL, \n\tCONSTRAINT uq_program_versions_tenant_id_id UNIQUE (tenant_id, id), \n\tCONSTRAINT uq_program_versions_workspace_key_tag UNIQUE (workspace_id, program_key, version_tag), \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE prompt_snapshots (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\toptimization_run_id UUID NOT NULL, \n\tpredictor_name VARCHAR(255) NOT NULL, \n\tprompt_type prompt_snapshot_type NOT NULL, \n\tprompt_text TEXT NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_prompt_snapshots_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_prompt_snapshots_run_id__optimization_runs_id FOREIGN KEY(optimization_run_id) REFERENCES optimization_runs (id) ON DELETE CASCADE, \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE artifacts (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\tsession_id UUID, \n\tturn_id UUID, \n\trun_id UUID, \n\tstep_id UUID, \n\tevent_id UUID, \n\tkind artifact_kind NOT NULL, \n\tprovider artifact_provider DEFAULT 'memory' NOT NULL, \n\turi TEXT NOT NULL, \n\tpath TEXT, \n\tmime_type VARCHAR(255), \n\tsize_bytes BIGINT, \n\tchecksum VARCHAR(255), \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_artifacts_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_artifacts_run_id__execution_runs_id FOREIGN KEY(run_id) REFERENCES execution_runs (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_artifacts_step_id__execution_steps_id FOREIGN KEY(step_id) REFERENCES execution_steps (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_artifacts_event_id__execution_events_id FOREIGN KEY(event_id) REFERENCES execution_events (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_artifacts_session_id__chat_sessions_id FOREIGN KEY(session_id) REFERENCES chat_sessions (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_artifacts_turn_id__chat_turns_id FOREIGN KEY(turn_id) REFERENCES chat_turns (id) ON DELETE SET NULL, \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
    "CREATE TABLE trace_feedback (\n\tid UUID DEFAULT app.uuid_v7() NOT NULL, \n\ttenant_id UUID NOT NULL, \n\tworkspace_id UUID NOT NULL, \n\texternal_trace_id UUID NOT NULL, \n\trun_id UUID, \n\tsession_id UUID, \n\tturn_id UUID, \n\tdataset_id UUID, \n\treviewer_user_id UUID, \n\tis_correct BOOLEAN NOT NULL, \n\tcomment TEXT, \n\texpected_response TEXT, \n\tmetadata JSONB DEFAULT '{}'::jsonb NOT NULL, \n\tcreated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, \n\tPRIMARY KEY (id), \n\tCONSTRAINT fk_trace_feedback_tenant_workspace__workspaces_tenant_id_id FOREIGN KEY(tenant_id, workspace_id) REFERENCES workspaces (tenant_id, id) ON DELETE CASCADE, \n\tCONSTRAINT fk_trace_feedback_reviewer_user_id__users_id FOREIGN KEY(reviewer_user_id) REFERENCES users (id) ON DELETE SET NULL, \n\tCONSTRAINT fk_trace_feedback_external_trace_id__external_traces_id FOREIGN KEY(external_trace_id) REFERENCES external_traces (id) ON DELETE CASCADE, \n\tFOREIGN KEY(tenant_id) REFERENCES tenants (id) ON DELETE CASCADE\n);",
]

_BASELINE_INDEX_SQL = [
    "CREATE INDEX ix_tenants_status ON tenants (status);",
    "CREATE INDEX ix_tenant_subscriptions_tenant_created_at ON tenant_subscriptions (tenant_id, created_at);",
    "CREATE INDEX ix_tenant_subscriptions_status ON tenant_subscriptions (status);",
    "CREATE INDEX ix_users_tenant_email ON users (tenant_id, email);",
    "CREATE INDEX ix_users_tenant_created_at ON users (tenant_id, created_at);",
    "CREATE INDEX ix_tenant_memberships_tenant_created_at ON tenant_memberships (tenant_id, created_at);",
    "CREATE INDEX ix_workspaces_tenant_status ON workspaces (tenant_id, status);",
    "CREATE INDEX ix_workspaces_tenant_updated_at ON workspaces (tenant_id, updated_at);",
    "CREATE INDEX ix_chat_sessions_workspace_updated_at ON chat_sessions (workspace_id, updated_at);",
    "CREATE INDEX ix_chat_sessions_workspace_status ON chat_sessions (workspace_id, status);",
    "CREATE INDEX ix_jobs_workspace_created_at ON jobs (workspace_id, created_at);",
    "CREATE INDEX ix_jobs_workspace_status_available ON jobs (workspace_id, status, available_at);",
    "CREATE INDEX ix_jobs_status_available_at ON jobs (status, available_at);",
    "CREATE INDEX ix_memory_items_workspace_created_at ON memory_items (workspace_id, created_at);",
    "CREATE INDEX ix_memory_items_status ON memory_items (workspace_id, status, created_at);",
    "CREATE INDEX ix_memory_items_tags ON memory_items USING gin (tags);",
    "CREATE INDEX ix_memory_items_workspace_scope ON memory_items (workspace_id, scope, scope_id, created_at);",
    "CREATE INDEX ix_optimization_modules_workspace_created_at ON optimization_modules (workspace_id, created_at);",
    "CREATE INDEX ix_outbox_events_status_available_workspace ON outbox_events (status, available_at, workspace_id);",
    "CREATE INDEX ix_outbox_events_workspace_created_at ON outbox_events (workspace_id, created_at);",
    "CREATE INDEX ix_sandbox_sessions_workspace_status ON sandbox_sessions (workspace_id, status);",
    "CREATE INDEX ix_sandbox_sessions_workspace_created_at ON sandbox_sessions (workspace_id, created_at);",
    "CREATE INDEX ix_workspace_memberships_tenant_workspace ON workspace_memberships (tenant_id, workspace_id, created_at);",
    "CREATE INDEX ix_workspace_runtime_settings_tenant_workspace ON workspace_runtime_settings (tenant_id, workspace_id);",
    "CREATE INDEX ix_workspace_volumes_workspace_updated_at ON workspace_volumes (workspace_id, updated_at);",
    "CREATE INDEX ix_workspace_volumes_workspace_status ON workspace_volumes (workspace_id, status);",
    "CREATE INDEX ix_chat_turns_session_created_at ON chat_turns (session_id, created_at);",
    "CREATE INDEX ix_chat_turns_workspace_created_at ON chat_turns (workspace_id, created_at);",
    "CREATE INDEX ix_datasets_workspace_created_at ON datasets (workspace_id, created_at);",
    "CREATE INDEX ix_datasets_workspace_module_created_at ON datasets (workspace_id, optimization_module_id, created_at);",
    "CREATE INDEX ix_memory_links_workspace_created_at ON memory_links (workspace_id, created_at);",
    "CREATE INDEX ix_memory_links_target_lookup ON memory_links (workspace_id, target_kind, target_id);",
    "CREATE INDEX ix_session_state_snapshots_session_created_at ON session_state_snapshots (session_id, created_at);",
    "CREATE INDEX ix_volume_objects_workspace_volume_modified ON volume_objects (workspace_volume_id, modified_at);",
    "CREATE INDEX ix_volume_objects_workspace_path ON volume_objects (workspace_id, path);",
    "CREATE INDEX ix_dataset_examples_dataset_row_index ON dataset_examples (dataset_id, row_index);",
    "CREATE INDEX ix_execution_runs_workspace_created_at ON execution_runs (workspace_id, created_at);",
    "CREATE INDEX ix_execution_runs_workspace_status_created_at ON execution_runs (workspace_id, status, created_at);",
    "CREATE INDEX ix_optimization_runs_workspace_created_at ON optimization_runs (workspace_id, created_at);",
    "CREATE INDEX ix_optimization_runs_workspace_status ON optimization_runs (workspace_id, status);",
    "CREATE INDEX ix_evaluation_results_run_example_index ON evaluation_results (optimization_run_id, example_index);",
    "CREATE INDEX ix_execution_events_run_sequence ON execution_events (run_id, sequence);",
    "CREATE INDEX ix_execution_events_workspace_created_at ON execution_events (workspace_id, created_at);",
    "CREATE INDEX ix_execution_steps_workspace_type_created_at ON execution_steps (workspace_id, step_type, created_at);",
    "CREATE INDEX ix_execution_steps_run_step ON execution_steps (run_id, step_index);",
    "CREATE INDEX ix_external_traces_workspace_observed_at ON external_traces (workspace_id, observed_at);",
    "CREATE INDEX ix_external_traces_client_request_id ON external_traces (client_request_id);",
    "CREATE INDEX ix_program_versions_workspace_created_at ON program_versions (workspace_id, created_at);",
    "CREATE INDEX ix_program_versions_workspace_status ON program_versions (workspace_id, status);",
    "CREATE INDEX ix_prompt_snapshots_run_created_at ON prompt_snapshots (optimization_run_id, created_at);",
    "CREATE INDEX ix_artifacts_workspace_run_created_at ON artifacts (workspace_id, run_id, created_at);",
    "CREATE INDEX ix_artifacts_workspace_created_at ON artifacts (workspace_id, created_at);",
    "CREATE INDEX ix_artifacts_workspace_kind_created_at ON artifacts (workspace_id, kind, created_at);",
    "CREATE INDEX ix_trace_feedback_external_trace_created_at ON trace_feedback (external_trace_id, created_at);",
    "CREATE INDEX ix_trace_feedback_workspace_created_at ON trace_feedback (workspace_id, created_at);",
]


def _drop_public_objects_except_alembic() -> None:
    op.execute(
        """
        DO $$
        DECLARE row record;
        BEGIN
          FOR row IN
            SELECT schemaname, matviewname AS object_name
            FROM pg_matviews
            WHERE schemaname = 'public'
          LOOP
            EXECUTE format(
              'DROP MATERIALIZED VIEW IF EXISTS %I.%I CASCADE',
              row.schemaname,
              row.object_name
            );
          END LOOP;

          FOR row IN
            SELECT schemaname, viewname AS object_name
            FROM pg_views
            WHERE schemaname = 'public'
          LOOP
            EXECUTE format(
              'DROP VIEW IF EXISTS %I.%I CASCADE',
              row.schemaname,
              row.object_name
            );
          END LOOP;

          FOR row IN
            SELECT tablename AS object_name
            FROM pg_tables
            WHERE schemaname = 'public'
              AND tablename <> 'alembic_version'
          LOOP
            EXECUTE format(
              'DROP TABLE IF EXISTS public.%I CASCADE',
              row.object_name
            );
          END LOOP;

          FOR row IN
            SELECT sequence_name AS object_name
            FROM information_schema.sequences
            WHERE sequence_schema = 'public'
          LOOP
            EXECUTE format(
              'DROP SEQUENCE IF EXISTS public.%I CASCADE',
              row.object_name
            );
          END LOOP;

          FOR row IN
            SELECT t.typname AS object_name
            FROM pg_type t
            JOIN pg_namespace n ON n.oid = t.typnamespace
            WHERE n.nspname = 'public'
              AND t.typtype = 'e'
          LOOP
            EXECUTE format(
              'DROP TYPE IF EXISTS public.%I CASCADE',
              row.object_name
            );
          END LOOP;
        END $$;
        """
    )


def _install_uuid_v7_helper() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS app")
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
    op.execute(
        """
        CREATE OR REPLACE FUNCTION app.uuid_v7()
        RETURNS uuid
        LANGUAGE plpgsql
        AS $$
        DECLARE
          unix_ts_ms BIGINT;
          ts_hex TEXT;
          rand_hex TEXT;
          variant_source INTEGER;
          variant_nibble TEXT;
        BEGIN
          IF to_regprocedure('uuidv7()') IS NOT NULL THEN
            RETURN uuidv7();
          ELSIF to_regprocedure('uuid_generate_v7()') IS NOT NULL THEN
            RETURN uuid_generate_v7();
          ELSIF to_regprocedure('gen_random_bytes(integer)') IS NOT NULL THEN
            unix_ts_ms := floor(extract(epoch from clock_timestamp()) * 1000);
            ts_hex := lpad(to_hex(unix_ts_ms), 12, '0');
            rand_hex := substr(encode(gen_random_bytes(10), 'hex'), 1, 19);
            variant_source := get_byte(
              decode('0' || substr(rand_hex, 4, 1), 'hex'),
              0
            );
            variant_nibble := substr('89ab', (variant_source & 3) + 1, 1);

            RETURN (
              substr(ts_hex, 1, 8) || '-' ||
              substr(ts_hex, 9, 4) || '-' ||
              '7' || substr(rand_hex, 1, 3) || '-' ||
              variant_nibble || substr(rand_hex, 5, 3) || '-' ||
              substr(rand_hex, 8, 12)
            )::uuid;
          END IF;

          RAISE EXCEPTION
            'No UUIDv7-compatible generator found (expected uuidv7(), uuid_generate_v7(), or pgcrypto''s gen_random_bytes(integer))';
        END;
        $$;
        """
    )


def _execute_statements(statements: Iterable[str]) -> None:
    for statement in statements:
        op.execute(statement)


def _create_target_schema() -> None:
    _execute_statements(_BASELINE_ENUM_SQL)
    _execute_statements(_BASELINE_TABLE_SQL)
    _execute_statements(_BASELINE_INDEX_SQL)


def _apply_rls(table_name: str, *, include_workspace_scope: bool) -> None:
    policy_name = f"tenant_scope_{table_name}"
    tenant_condition = (
        "tenant_id = nullif(current_setting('app.tenant_id', true), '')::uuid"
    )
    workspace_condition = (
        "(nullif(current_setting('app.workspace_id', true), '') IS NULL "
        "OR workspace_id = nullif(current_setting('app.workspace_id', true), '')::uuid)"
    )
    condition = (
        f"{tenant_condition} AND {workspace_condition}"
        if include_workspace_scope
        else tenant_condition
    )

    op.execute(f"ALTER TABLE public.{table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE public.{table_name} FORCE ROW LEVEL SECURITY")
    op.execute(f"DROP POLICY IF EXISTS {policy_name} ON public.{table_name}")
    op.execute(
        f"""
        CREATE POLICY {policy_name}
        ON public.{table_name}
        USING ({condition})
        WITH CHECK ({condition})
        """
    )


def _apply_optimization_run_recovery_policy() -> None:
    policy_name = "maintenance_recover_stale_optimization_runs"
    op.execute(f"DROP POLICY IF EXISTS {policy_name} ON public.optimization_runs")
    op.execute(
        f"""
        CREATE POLICY {policy_name}
        ON public.optimization_runs
        FOR UPDATE
        USING (
          coalesce(current_setting('app.maintenance_task', true), '') =
            'recover_stale_optimization_runs'
          AND status = 'running'
        )
        WITH CHECK (
          coalesce(current_setting('app.maintenance_task', true), '') =
            'recover_stale_optimization_runs'
        )
        """
    )


def _apply_target_rls() -> None:
    for table_name in _TENANT_ONLY_RLS_TABLES:
        _apply_rls(table_name, include_workspace_scope=False)
    for table_name in _WORKSPACE_RLS_TABLES:
        _apply_rls(table_name, include_workspace_scope=True)
    _apply_optimization_run_recovery_policy()


def upgrade() -> None:
    bind = op.get_bind()
    table_names = (
        bind.execute(
            text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_name != 'alembic_version'"
            )
        )
        .scalars()
        .all()
    )
    if table_names:
        total_rows = sum(
            bind.execute(text(f"SELECT COUNT(*) FROM public.{name}")).scalar() or 0
            for name in table_names
        )
        if total_rows > 0:
            raise RuntimeError(
                "0010_target_postgres_schema is a destructive clean-break baseline. "
                f"Detected {total_rows} rows across {len(table_names)} existing tables. "
                "Migrate data manually or provision a fresh database."
            )
    _drop_public_objects_except_alembic()
    _install_uuid_v7_helper()
    _create_target_schema()
    _apply_target_rls()


def downgrade() -> None:
    raise RuntimeError(
        "0010_target_postgres_schema is a destructive clean-break baseline migration and "
        "does not support automatic downgrade."
    )
