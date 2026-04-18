# Database Architecture

Fleet-RLM uses Neon/Postgres as the canonical durable data store for product state.
As of migration `0010_target_postgres_schema`, the runtime persistence contract is
tenant + workspace scoped, and all new primary keys are generated with `app.uuid_v7()`.

## Locations

| Component | Location |
| --- | --- |
| Engine and session lifecycle | `src/fleet_rlm/integrations/database/engine.py` |
| Repository boundary | `src/fleet_rlm/integrations/database/repository.py` |
| Shared repository context helpers | `src/fleet_rlm/integrations/database/repository_shared.py` |
| SQLAlchemy models | `src/fleet_rlm/integrations/database/models_*.py` |
| Typed request DTOs | `src/fleet_rlm/integrations/database/types.py` |
| Alembic migrations | `migrations/versions/` |

## Schema Baseline

`0010_target_postgres_schema` is a clean-break baseline migration:

- Drops legacy `public` objects (except `alembic_version`) and recreates schema from model metadata.
- Installs `app.uuid_v7()` with compatibility fallback:
  - Uses native `uuidv7()` when available.
  - Falls back to `uuid_generate_v7()` when available.
  - Falls back to `gen_random_uuid()` if neither v7 function exists.
- Reapplies baseline RLS policies for tenant/workspace-scoped tables.

This migration is intentionally destructive and is designed for disposable/dev databases and clean branch cutovers.

## Bounded Context Tables

### Identity and workspace

- `tenants`
- `users`
- `tenant_memberships`
- `workspaces`
- `workspace_memberships`
- `workspace_runtime_settings`

### Runtime and session history

- `chat_sessions`
- `chat_turns`
- `execution_runs`
- `execution_steps`
- `execution_events`
- `session_state_snapshots`

### Sandbox, volumes, and artifacts

- `sandbox_sessions`
- `workspace_volumes`
- `volume_objects`
- `artifacts`

### Memory

- `memory_items`
- `memory_links`

### Optimization and datasets

- `optimization_modules`
- `datasets`
- `dataset_examples`
- `optimization_runs`
- `evaluation_results`
- `prompt_snapshots`
- `program_versions`

### Trace feedback

- `external_traces`
- `trace_feedback`

### Jobs and billing

- `jobs`
- `outbox_events`
- `tenant_subscriptions`

## Request Context and RLS

Repository operations set transaction-local request context before tenant/workspace data access:

- `app.tenant_id`
- `app.user_id`
- `app.workspace_id`

RLS policies are enabled and forced on tenant/workspace scoped tables and evaluate
the context above. `app.workspace_id` is optional at query time; repository helpers
auto-resolve a default workspace when callers provide only `tenant_id`.

## Environment

| Variable | Description |
| --- | --- |
| `DATABASE_URL` | Pooled Postgres connection string for the application runtime (required when database-backed runtime is enabled) |
| `DATABASE_ADMIN_URL` | Direct Postgres connection string for Alembic, schema management, and admin/debug scripts. Falls back to `DATABASE_URL` when unset. |
| `DATABASE_REQUIRED` | Enforces startup failure when database is unavailable |

## Operational Commands

```bash
# Apply migrations
DATABASE_ADMIN_URL=postgresql://... uv run alembic upgrade head

# Generate/validate OpenAPI after persistence-facing API changes
uv run python scripts/openapi_tools.py generate
uv run python scripts/openapi_tools.py validate

# DB integration validation (requires disposable DATABASE_URL)
uv run pytest -q tests/integration/test_db_migrations.py tests/integration/test_db_repository.py
```

## Notes

- SQLite sidecar persistence remains available only for local legacy/import workflows; Postgres is the source of truth for durable product state.
- Large file bytes remain in Daytona volumes/object storage; Postgres stores metadata and linkable indexes.
- Neon guidance for this repo is: pooled URL for runtime traffic, direct non-pooler URL for migrations/admin work, and disposable branches for destructive validation before touching long-lived branches.
