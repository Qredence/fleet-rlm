# Database Architecture

Fleet-RLM uses **Neon/PostgreSQL** as its primary persistence layer with SQLAlchemy ORM, Alembic migrations, and tenant isolation via Row-Level Security (RLS).

## Overview

| Component | Location |
|-----------|----------|
| Engine/session management | `src/fleet_rlm/integrations/database/engine.py` |
| Repository layer | `src/fleet_rlm/integrations/database/repository.py` |
| Domain models | `src/fleet_rlm/integrations/database/models.py` |
| Type definitions | `src/fleet_rlm/integrations/database/types.py` |
| Migrations | `migrations/` (Alembic) |

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `DATABASE_URL` | Conditional | PostgreSQL connection string. Required when `DATABASE_REQUIRED=true`. |
| `DATABASE_REQUIRED` | No | When `true`, server fails startup if database is unavailable. Defaults to `true` in staging/production and when `AUTH_MODE=entra`. |

Example connection string format:

```text
# Neon
DATABASE_URL=postgresql://user:password@host.example.com/dbname?sslmode=require

# Local PostgreSQL
DATABASE_URL=postgresql://user:password@localhost:5432/dbname
```

## Connection Management

The `DatabaseManager` class in `engine.py` handles async database connections:

```python
from fleet_rlm.integrations.database.engine import DatabaseManager

# Initialize with connection URL
db = DatabaseManager(database_url)

# Async session context
async with db.session() as session:
    result = await session.execute(select(User))
```

### URL Normalization

The engine provides helpers for driver compatibility:

- `to_async_database_url(url)` — Normalizes for `asyncpg` driver
- `to_sync_database_url(url)` — Normalizes for `psycopg` driver (used by Alembic)

---

## Model Classes

All models inherit from `Base` and are defined in `src/fleet_rlm/integrations/database/models.py`.

### Core Models

#### Tenant

Multi-tenant organization entity. Each tenant is identified by their Entra ID tenant ID.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key, auto-generated |
| `entra_tenant_id` | String(128) | Entra ID tenant identifier (unique) |
| `slug` | String(128) | URL-friendly identifier (unique, optional) |
| `display_name` | String(255) | Human-readable name |
| `domain` | String(255) | Associated domain |
| `plan` | Enum | `free`, `team`, or `enterprise` |
| `status` | Enum | `active`, `suspended`, or `deleted` |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

#### User

User account within a tenant. Linked to Entra ID user accounts.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `entra_user_id` | String(128) | Entra ID user identifier |
| `email` | String(320) | User email (optional) |
| `full_name` | String(255) | Display name (optional) |
| `is_active` | Boolean | Account active status |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

**Unique constraints:** `(tenant_id, entra_user_id)`, `(tenant_id, id)`

#### Membership

Associates users with tenants and defines their role.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `user_id` | UUID | FK to `users.id` (composite) |
| `role` | Enum | `owner`, `admin`, `member`, or `viewer` |
| `is_default` | Boolean | Whether this is the user's default membership |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

### Sandbox & Execution Models

#### SandboxSession

Represents a sandbox execution environment session (Modal, Daytona, etc.).

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `created_by_user_id` | UUID | FK to `users.id` (composite, optional) |
| `provider` | Enum | `modal`, `daytona`, `aca_jobs`, or `local` |
| `external_id` | String(255) | Provider's session identifier |
| `status` | Enum | `active`, `ended`, or `failed` |
| `started_at` | DateTime | Session start time |
| `ended_at` | DateTime | Session end time (nullable) |
| `metadata_json` | JSONB | Arbitrary metadata |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

**Unique constraints:** `(tenant_id, provider, external_id)`

#### ModalVolume

Tracks Modal volume instances for persistent storage.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `provider` | String(32) | Provider name (default: `modal`) |
| `volume_name` | String(255) | Volume identifier |
| `external_volume_id` | String(255) | External volume ID (optional) |
| `environment` | String(64) | Modal environment (optional) |
| `region` | String(64) | Deployment region (optional) |
| `metadata_json` | JSONB | Arbitrary metadata |
| `last_seen_at` | DateTime | Last access time |
| `last_synced_at` | DateTime | Last sync time |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

### Run & Trace Models

#### Run

Represents a single agent execution/run.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `external_run_id` | String(255) | External run identifier |
| `created_by_user_id` | UUID | FK to `users.id` (composite) |
| `status` | Enum | `queued`, `running`, `completed`, `failed`, `cancelled` |
| `model_provider` | String(128) | LLM provider name (optional) |
| `model_name` | String(255) | Model identifier (optional) |
| `sandbox_provider` | Enum | Sandbox provider used |
| `sandbox_session_id` | UUID | FK to `sandbox_sessions.id` (composite, optional) |
| `error_json` | JSONB | Error details (nullable) |
| `started_at` | DateTime | Run start time |
| `completed_at` | DateTime | Run completion time (nullable) |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

#### RunStep

Individual step within a run execution.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `run_id` | UUID | FK to `runs.id` (composite) |
| `step_index` | Integer | Step order within run |
| `step_type` | Enum | See RunStepType enum below |
| `input_json` | JSONB | Step input data (nullable) |
| `output_json` | JSONB | Step output data (nullable) |
| `sandbox_session_external_id` | String(255) | External sandbox reference |
| `modal_volume_id` | UUID | FK to `modal_volumes.id` (composite, optional) |
| `modal_volume_name` | String(255) | Volume name (optional) |
| `cost_usd_micros` | BigInteger | Cost in micros USD (optional) |
| `tokens_in` | Integer | Input tokens (optional) |
| `tokens_out` | Integer | Output tokens (optional) |
| `latency_ms` | Integer | Step latency in milliseconds |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

**RunStepType Enum:** `tool_call`, `repl_exec`, `llm_call`, `retrieval`, `guardrail`, `summary`, `memory`, `output`, `status`

#### Artifact

Files and data produced during run execution.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `run_id` | UUID | FK to `runs.id` (composite) |
| `step_id` | UUID | FK to `run_steps.id` (composite, optional) |
| `kind` | Enum | `file`, `log`, `report`, `trace`, `image`, `data` |
| `uri` | Text | Storage URI |
| `mime_type` | String(255) | Content type (optional) |
| `size_bytes` | BigInteger | File size (optional) |
| `checksum` | String(255) | Content hash (optional) |
| `metadata_json` | JSONB | Arbitrary metadata |
| `created_at` | DateTime | Creation timestamp |

### RLM & DSPy Models

#### RLMProgram

Compiled DSPy programs for RLM execution.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `program_key` | String(255) | Unique program identifier |
| `display_name` | String(255) | Human-readable name (optional) |
| `kind` | String(64) | Program kind (default: `compiled`) |
| `status` | String(32) | Program status (default: `active`) |
| `dspy_signature` | String(255) | DSPy signature class (optional) |
| `version_tag` | String(128) | Version identifier (optional) |
| `schema_version` | Integer | Schema version (default: 1) |
| `source_run_id` | UUID | Source run reference (optional) |
| `created_by_user_id` | UUID | Creator reference (optional) |
| `program_json` | JSONB | Serialized program state |
| `metadata_json` | JSONB | Arbitrary metadata |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

#### RLMTrace

Captured execution traces from RLM runs.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `run_id` | UUID | FK to `runs.id` (composite) |
| `run_step_id` | UUID | FK to `run_steps.id` (composite, optional) |
| `program_id` | UUID | FK to `rlm_programs.id` (composite, optional) |
| `trace_kind` | String(64) | Trace type (default: `trajectory`) |
| `status` | String(32) | Trace status (default: `captured`) |
| `source` | String(64) | Trace source (default: `rlm`) |
| `summary_text` | Text | Human-readable summary (optional) |
| `payload_json` | JSONB | Trace payload data |
| `tokens_in` | Integer | Input tokens (optional) |
| `tokens_out` | Integer | Output tokens (optional) |
| `latency_ms` | Integer | Latency in milliseconds |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

### Memory Model

#### MemoryItem

Persistent memory storage for agents.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `scope` | Enum | `user`, `tenant`, `run`, or `agent` |
| `scope_id` | String(255) | Entity identifier within scope |
| `kind` | Enum | `note`, `summary`, `fact`, `preference`, `context` |
| `uri` | Text | Content URI (optional) |
| `content_text` | Text | Text content (optional) |
| `content_json` | JSONB | JSON content (optional) |
| `source` | Enum | `user_input`, `system`, `tool`, `llm`, `imported` |
| `importance` | SmallInteger | Importance score (0-100) |
| `tags` | Array[Text] | Searchable tags |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

### Job Queue Model

#### Job

Asynchronous job queue for background processing.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `job_type` | Enum | `run_task`, `memory_compaction`, `evaluation`, `maintenance` |
| `status` | Enum | `queued`, `leased`, `running`, `succeeded`, `failed`, `dead` |
| `payload` | JSONB | Job payload data |
| `attempts` | Integer | Number of processing attempts |
| `max_attempts` | Integer | Maximum retry attempts (default: 5) |
| `available_at` | DateTime | When job becomes available |
| `locked_at` | DateTime | When job was locked (nullable) |
| `locked_by` | String(255) | Worker that locked the job |
| `idempotency_key` | String(255) | Unique job identifier |
| `last_error` | JSONB | Last error details (nullable) |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

### Billing Model

#### TenantSubscription

Subscription and billing information.

| Field | Type | Description |
|-------|------|-------------|
| `id` | UUID | Primary key |
| `tenant_id` | UUID | FK to `tenants.id` |
| `billing_source` | Enum | `azure_marketplace` or `manual` |
| `purchaser_tenant_id` | String(128) | Purchaser tenant ID (optional) |
| `subscription_id` | String(255) | Subscription identifier |
| `offer_id` | String(255) | Offer identifier (optional) |
| `plan_id` | String(255) | Plan identifier (optional) |
| `status` | Enum | `trial`, `active`, `past_due`, `cancelled`, `expired` |
| `started_at` | DateTime | Subscription start |
| `ended_at` | DateTime | Subscription end (nullable) |
| `created_at` | DateTime | Creation timestamp |
| `updated_at` | DateTime | Last update timestamp |

---

## Tenant Isolation (RLS)

Fleet-RLM uses PostgreSQL Row-Level Security (RLS) to enforce tenant isolation at the database level. This provides defense-in-depth beyond application-level filtering.

### How RLS Works

1. **Session Context:** Before each database operation, the repository sets the tenant context:

   ```sql
   SELECT set_config('app.tenant_id', '<tenant_uuid>', true);
   ```

2. **Policy Enforcement:** RLS policies compare the `tenant_id` column against the session context:

   ```sql
   CREATE POLICY tenant_isolation_users
   ON users
   USING (tenant_id = current_setting('app.tenant_id', true)::uuid)
   WITH CHECK (tenant_id = current_setting('app.tenant_id', true)::uuid);
   ```

3. **Force RLS:** Tables have RLS forced, ensuring even superusers are subject to policies:

   ```sql
   ALTER TABLE users ENABLE ROW LEVEL SECURITY;
   ALTER TABLE users FORCE ROW LEVEL SECURITY;
   ```

### Tables with RLS

The following tables have RLS policies:

- `users`
- `memberships`
- `sandbox_sessions`
- `modal_volumes`
- `runs`
- `run_steps`
- `artifacts`
- `rlm_programs`
- `rlm_traces`
- `memory_items`
- `jobs`
- `tenant_subscriptions`

### Repository Context Setting

The `FleetRepository` automatically sets tenant context in each session:

```python
async def _set_request_context(
    self,
    session: AsyncSession,
    tenant_id: uuid.UUID | str,
    user_id: uuid.UUID | str | None = None,
) -> None:
    await session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    await session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": "" if user_id is None else str(user_id)},
    )
```

---

## Repository Methods

The `FleetRepository` class in `src/fleet_rlm/integrations/database/repository.py` provides typed, tenant-scoped database operations.

### Identity & Authentication

| Method | Description |
|--------|-------------|
| `upsert_tenant(entra_tenant_id, slug, display_name, domain)` | Create or update tenant by Entra ID |
| `upsert_user(tenant_id, entra_user_id, email, full_name, ...)` | Create or update user with optional membership |
| `upsert_identity(entra_tenant_id, entra_user_id, email, full_name)` | Combined tenant + user upsert |
| `resolve_tenant_by_entra_claim(entra_tenant_id)` | Look up tenant by Entra tenant ID |
| `resolve_user_by_entra_claim(tenant_id, entra_user_id)` | Look up user by Entra user ID |
| `resolve_control_plane_identity(entra_tenant_id, entra_user_id, ...)` | Full identity resolution with membership |

### Run Operations

| Method | Description |
|--------|-------------|
| `create_run(request: RunCreateRequest)` | Create a new run |
| `update_run_status(tenant_id, run_id, status, error_json)` | Update run status |
| `get_run_steps(tenant_id, run_id)` | Get all steps for a run |
| `append_step(request: RunStepCreateRequest)` | Add a step to a run |

### Artifact Operations

| Method | Description |
|--------|-------------|
| `store_artifact(request: ArtifactCreateRequest)` | Store an artifact |

### Memory Operations

| Method | Description |
|--------|-------------|
| `store_memory_item(request: MemoryItemCreateRequest)` | Store a memory item |
| `list_memory_items(tenant_id, scope, scope_id, limit)` | Query memory items |

### Job Queue Operations

| Method | Description |
|--------|-------------|
| `create_job(request: JobCreateRequest)` | Create a job with idempotency |
| `lease_jobs(request: JobLeaseRequest)` | Lease available jobs for processing |

### Sandbox Operations

| Method | Description |
|--------|-------------|
| `upsert_sandbox_session(tenant_id, provider, external_id, ...)` | Create or update sandbox session |

---

## Migration Workflow

Migrations are managed with Alembic in the `migrations/` directory.

### Migration Files

| File | Description |
|------|-------------|
| `alembic.ini` | Alembic configuration |
| `migrations/env.py` | Migration environment setup |
| `migrations/script.py.mako` | Migration template |
| `migrations/versions/*.py` | Versioned migration scripts |

### Version History

| Revision | Description |
|----------|-------------|
| `0001_neon_core_schema` | Initial multi-tenant schema with RLS |
| `0002_tenant_fk_hardening` | Foreign key constraint hardening |
| `0003_skills_taxonomy_and_usage` | Skills and usage tracking |
| `0004_remove_deprecated_skills_taxonomy` | Remove deprecated tables |
| `0005_rlm_programs_and_traces` | RLM program and trace models |
| `0006_modal_infra_tracking` | Modal infrastructure tracking |
| `0007_neon_performance_indexes` | Performance optimization indexes |
| `0008_neon_control_plane_consolidation` | Control plane consolidation |

### Running Migrations

```bash
# Set DATABASE_URL (replace with your actual connection string)
export DATABASE_URL="postgresql://<user>:<password>@<host>/<database>"

# View current revision
uv run alembic current

# View migration history
uv run alembic history

# Apply migrations to latest
uv run alembic upgrade head

# Rollback one migration
uv run alembic downgrade -1

# Generate new migration
uv run alembic revision --autogenerate -m "description"
```

### Migration Best Practices

1. **Always test migrations** in a staging environment before production
2. **Use `--autogenerate` with caution** — review generated migrations
3. **Add RLS policies** for new tenant-scoped tables
4. **Include downgrade paths** for rollback capability
5. **Use idempotent operations** where possible

---

## Runtime Behavior

The server startup behavior in `src/fleet_rlm/api/main.py`:

1. If `DATABASE_URL` is configured, initialize Neon repository
2. If `DATABASE_REQUIRED=true` and URL is missing, fail startup
3. If database is optional and unavailable, continue with persistence-disabled warnings

### Configuration

Database settings in `ServerRuntimeConfig`:

| Setting | Default | Description |
|---------|---------|-------------|
| `database_url` | `DATABASE_URL` env var | Connection string |
| `database_required` | `true` in staging/production | Fail if database unavailable |
| `db_echo` | `false` | Echo SQL statements |
| `db_validate_on_startup` | `false` | Ping database on startup |

### Startup Validation

In production environments:

```python
if self.database_required and not self.database_url:
    raise ValueError("DATABASE_URL is required when database_required=true")
```

When using Entra authentication:

```python
if self.auth_mode == "entra":
    if not self.database_required:
        raise ValueError("DATABASE_REQUIRED must be true when AUTH_MODE=entra")
```

---

## Index Reference

Key indexes for performance optimization:

### Run Indexes
- `ix_runs_tenant_created_at` — Tenant-scoped run listing
- `ix_runs_tenant_status_created_at` — Status-filtered queries

### RunStep Indexes
- `ix_run_steps_tenant_run_step` — Step ordering within runs
- `ix_run_steps_tenant_type_created_at` — Type-filtered queries

### Memory Indexes
- `ix_memory_items_scope` — Scope-scoped queries
- `ix_memory_items_tags` — GIN index for tag array searches

### Job Queue Indexes
- `ix_jobs_status_available_at` — Job queue polling
- `ix_jobs_tenant_status_available` — Tenant-scoped job leasing
