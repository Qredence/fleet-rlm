# Database

Canonical Run Environment set: `daytona`.

Set a Daytona profile in `config/fleet.toml` before starting this backend. The
committed profiles and their provider environment names are listed in the
[profile matrix](profile-matrix.md):

| Profile | Code execution | LLM calls | Durable volume | Auth/scope |
| --- | --- | --- | --- | --- |
| `daytona-recursive` (default) | Daytona Sandbox Code Interpreter | real `dspy.LM` | Workspace Volume | local scope |

Daytona is the full Fleet solution with Workspace Volume Scope and Turn Commit
promotion. Private deterministic tests use an in-memory composition and do not
represent another public runtime profile.

For disposable PostgreSQL or production, Fleet RLM starts from an empty
database and one Alembic baseline under `migrations/versions/`.

```bash
export FLEET_DATABASE_URL='postgresql+asyncpg://...'
uv run python scripts/db_init.py
uv run alembic check
```

The canonical tables are `fleet_users`, `fleet_workspaces`, `fleet_sessions`,
`fleet_turns`, `fleet_runs`, `fleet_sandbox_bindings`, `fleet_attachments`, `fleet_artifacts`,
`fleet_skills`, and `fleet_memory_promotion_intents`. SQLAlchemy models live in
`fleet_rlm.persistence.models`.

Production startup assumes migrations have already run. Explicit SQLite
test/offline helpers may call `create_tables`; all other environments must use
Alembic.

## Data schema

Types are engine-agnostic as documented here: `uuid` is a native UUID on
PostgreSQL and a 32-character hex string on SQLite; `json` is portable JSON;
`timestamptz` is a time-zone-aware timestamp. Deletion semantics are uniform:
every foreign key cascates (`ON DELETE CASCADE`), so deleting a Session removes
its Runs, Turns, Bindings, Artifacts, and Memory promotion intents, and
deleting a Workspace removes its Sessions, Bindings, Attachments, Artifacts,
and Skill rows.

```text
fleet_users ─┬─< fleet_sessions >─┬─ fleet_workspaces
             │                    ├──< fleet_turns >── fleet_runs
             │                    ├──< fleet_runs
             │                    └─── fleet_sandbox_bindings >─┬─ fleet_workspaces
             ├─< fleet_attachments >─────────────────────────────┘
             ├─< fleet_artifacts >─< fleet_runs
             └─< fleet_memory_promotion_intents >─< fleet_runs
```

### fleet_users

Authenticated principals. A User row is created on first authenticated
Session scope.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | no | primary key |
| `external_subject` | `varchar(255)` | yes | unique when present; identity-provider subject |
| `created_at` | `timestamptz` | no | server default `now()` |

### fleet_workspaces

Durable Workspace scope owning Volumes and Attachments.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | no | primary key |
| `name` | `varchar(255)` | no | default `default` |
| `created_at` | `timestamptz` | no | server default `now()` |

### fleet_sessions

Durable conversational Session bound to one User and Workspace.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | no | primary key |
| `user_id` | `uuid` | no | FK → `fleet_users.id` (cascade) |
| `workspace_id` | `uuid` | no | FK → `fleet_workspaces.id` (cascade) |
| `status` | `varchar(32)` | no | default `active`; closed set below |
| `title` | `varchar(255)` | no | default `New Session` |
| `checkpoint_version` | `integer` | no | default `0`; optimistic History version |
| `created_at` | `timestamptz` | no | server default `now()` |
| `updated_at` | `timestamptz` | no | server default `now()` |

- Check `ck_fleet_sessions_status`: `status IN ('active', 'archived')`.
- Unique `uq_fleet_sessions_id_workspace`: `(id, workspace_id)` — the composite
  parent key that SandboxBinding lineage references.
- Index `ix_fleet_sessions_workspace_updated`: `(workspace_id, updated_at)`.

### fleet_runs

One execution attempt per durable Turn: claim authority, idempotency, and
settlement state. Partial unique indexes provide the concurrency fence.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | no | primary key |
| `session_id` | `uuid` | no | FK → `fleet_sessions.id` (cascade) |
| `status` | `varchar(32)` | no | default `running`; closed set below |
| `idempotency_key` | `varchar(128)` | no | Session-scoped client key |
| `input_fingerprint` | `varchar(64)` | no | SHA-256 of canonical Turn input |
| `base_checkpoint_version` | `integer` | no | Session version claimed against |
| `commit_checkpoint_version` | `integer` | yes | set only on completion |
| `claim_owner` | `varchar(128)` | yes | claim token; recovery owner during recovery |
| `claim_heartbeat_at` | `timestamptz` | yes | claim liveness stamp |
| `cancel_requested_at` | `timestamptz` | yes | advisory cancellation request |
| `failure_code` | `varchar(64)` | yes | typed terminal failure code |
| `failure_public_message` | `text` | yes | sanitized public failure message |
| `failure_usage_json` | `json` | yes | failure usage accounting |
| `terminal_intent` | `varchar(32)` | yes | settlement intent while settling |
| `recovery_metadata_json` | `json` | yes | bounded recovery retry metadata |
| `created_at` | `timestamptz` | no | server default `now()` |
| `finished_at` | `timestamptz` | yes | terminal transition stamp |

- Check `ck_fleet_runs_status`: `status IN ('running', 'settling', 'completed',
  'failed', 'cancelled', 'timeout')`.
- Check `ck_fleet_runs_fingerprint`: `length(input_fingerprint) = 64`.
- Check `ck_fleet_runs_terminal_shape`: each status must carry its required
  claim/commit/failure fields (running claims own a claim; completed carries a
  commit version and no claim; terminal failures carry a failure code and no
  claim or commit version).
- Unique partial index `uq_fleet_runs_live_idempotency`:
  `(session_id, idempotency_key)` where `status IN ('running', 'settling',
  'completed')` — one live or completed Run per idempotency key.
- Unique partial index `uq_fleet_runs_one_running`: `(session_id)` where
  `status IN ('running', 'settling')` — at most one active Run per Session.
- Index `ix_fleet_runs_session_created`: `(session_id, created_at)`.
- Index `ix_fleet_runs_recovery_scan`: `(status, claim_heartbeat_at)`.

### fleet_turns

Durable Turn records; exactly one user row and one assistant row per Run.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | no | primary key |
| `session_id` | `uuid` | no | FK → `fleet_sessions.id` (cascade) |
| `run_id` | `uuid` | no | FK → `fleet_runs.id` (cascade) |
| `sequence` | `integer` | no | Session-scoped ordering |
| `role` | `varchar(32)` | no | `user` or `assistant` |
| `user_input_json` | `json` | yes | versioned user input; user rows only |
| `committed_turn_json` | `json` | yes | canonical committed Turn; assistant rows only |
| `created_at` | `timestamptz` | no | server default `now()` |

- Check `ck_fleet_turns_role_shape`: user rows carry `user_input_json` and no
  committed Turn; assistant rows carry `committed_turn_json` and no input.
- Unique `uq_fleet_turns_session_sequence`: `(session_id, sequence)`.
- Unique `uq_fleet_turns_run_role`: `(run_id, role)`.
- Index `ix_fleet_turns_session_sequence`: `(session_id, sequence)`.

### fleet_sandbox_bindings

Per-Session Daytona Sandbox/Volume binding; the database row is the durable
authority for provider state.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | no | primary key |
| `session_id` | `uuid` | no | FK → `fleet_sessions.id` (cascade); unique |
| `sandbox_id` | `varchar(255)` | yes | provider sandbox identity when acquired |
| `workspace_id` | `uuid` | no | FK → `fleet_workspaces.id` (cascade) |
| `volume_id` | `varchar(255)` | yes | provider volume identity |
| `volume_subpath` | `varchar(512)` | no | mounted Volume subpath |
| `mount_path` | `varchar(512)` | no | default `/home/daytona/fleet` |
| `provider_state` | `varchar(64)` | no | default `missing`; closed set below |
| `last_verified_at` | `timestamptz` | yes | last provider verification |
| `created_at` | `timestamptz` | no | server default `now()` |
| `updated_at` | `timestamptz` | no | server default `now()` |

- Check `ck_fleet_sandbox_bindings_provider_state`: `provider_state IN
  ('missing', 'running', 'stopped', 'paused', 'archived', 'fencing',
  'quarantined', 'unrecoverable')`.
- Unique `(session_id)` — one binding per Session.
- Composite FK `fk_fleet_sandbox_bindings_session_workspace`:
  `(session_id, workspace_id)` → `fleet_sessions(id, workspace_id)` (cascade);
  a binding cannot name an unrelated Workspace.

### fleet_attachments

Authorized durable Attachment metadata; storage references stay private.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | no | primary key |
| `workspace_id` | `uuid` | no | FK → `fleet_workspaces.id` (cascade) |
| `user_id` | `uuid` | no | FK → `fleet_users.id` (cascade) |
| `filename` | `varchar(512)` | no | default empty |
| `content_type` | `varchar(255)` | yes | media type when known |
| `byte_size` | `integer` | no | default `0` |
| `checksum_sha256` | `varchar(64)` | no | hex digest |
| `storage_ref` | `text` | no | private storage reference |
| `created_at` | `timestamptz` | no | server default `now()` |

- Checks: `byte_size >= 0`; `length(checksum_sha256) = 64`.

### fleet_artifacts

Artifact metadata written only inside the successful Turn commit.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | no | primary key |
| `workspace_id` | `uuid` | no | FK → `fleet_workspaces.id` (cascade) |
| `user_id` | `uuid` | no | FK → `fleet_users.id` (cascade) |
| `session_id` | `uuid` | no | FK → `fleet_sessions.id` (cascade) |
| `run_id` | `uuid` | no | FK → `fleet_runs.id` (cascade) |
| `kind` | `varchar(64)` | no | default `text` |
| `title` | `varchar(512)` | yes | display title |
| `media_type` | `varchar(255)` | no | default `text/plain` |
| `byte_size` | `integer` | no | default `0` |
| `checksum_sha256` | `varchar(64)` | no | hex digest |
| `storage_ref` | `text` | no | private storage reference |
| `created_at` | `timestamptz` | no | server default `now()` |

- Checks: `byte_size >= 0`; `length(checksum_sha256) = 64`.
- Workspace/User/Session/Run parents are validated independently; composite
  lineage (Run and Session agreeing) is deferred (see SQLite policy section).

### fleet_skills

Retained SQL rows; the active bundled runtime catalog is in memory.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | no | primary key |
| `workspace_id` | `uuid` | yes | FK → `fleet_workspaces.id` (cascade); null is global |
| `name` | `varchar(255)` | no | skill name |
| `version` | `varchar(64)` | no | default `0.0.0` |
| `trust` | `varchar(32)` | no | default `system` |
| `visibility` | `varchar(32)` | no | default `workspace` |
| `metadata_json` | `json` | no | skill metadata |
| `created_at` | `timestamptz` | no | server default `now()` |

### fleet_memory_promotion_intents

Crash-recoverable autonomous Memory promotion intents (P23). Rows are inserted
only inside the successful Turn commit transaction and pinned to canonical v3
record bytes, so reconciliation replays are byte-identical and idempotent at
the mounted Workspace Agent.

| Column | Type | Null | Notes |
| --- | --- | --- | --- |
| `id` | `uuid` | no | primary key |
| `run_id` | `uuid` | no | FK → `fleet_runs.id` (cascade) |
| `session_id` | `uuid` | no | FK → `fleet_sessions.id` (cascade) |
| `workspace_id` | `uuid` | no | FK → `fleet_workspaces.id` (cascade) |
| `user_id` | `uuid` | no | FK → `fleet_users.id` (cascade) |
| `candidate_ordinal` | `integer` | no | candidate ordering within the Run |
| `candidate_id` | `varchar(12)` | no | exactly 12 characters |
| `category` | `varchar(64)` | no | Memory category |
| `learning` | `text` | no | candidate learning payload |
| `byte_size` | `integer` | no | `0`–`3904` |
| `supersedes_id` | `varchar(8)` | yes | superseded Memory id; 8 characters when set |
| `memory_id` | `varchar(8)` | no | exactly 8 characters |
| `record_text` | `text` | no | canonical pinned record text |
| `source` | `varchar(32)` | no | default `agent_candidate`; closed set below |
| `status` | `varchar(32)` | no | default `pending`; closed set below |
| `completion_reason` | `varchar(32)` | yes | terminal delivery reason |
| `promoted_memory_id` | `varchar(8)` | yes | promoted Memory identity |
| `attempts` | `integer` | no | default `0` |
| `last_attempt_at` | `timestamptz` | yes | last worker attempt stamp |
| `next_attempt_at` | `timestamptz` | no | server default `now()`; due-time scan key |
| `last_error` | `varchar(64)` | yes | bounded last failure category |
| `claim_owner` | `varchar(128)` | yes | CAS claim token |
| `claim_heartbeat_at` | `timestamptz` | yes | worker claim stamp |
| `created_at` | `timestamptz` | no | server default `now()` |
| `completed_at` | `timestamptz` | yes | terminal stamp |

- Check `ck_fleet_memory_intents_status`: `status IN ('pending', 'completing',
  'completed', 'failed')`.
- Check `ck_fleet_memory_intents_source`: `source IN ('agent_candidate')`.
- Checks: `length(candidate_id) = 12`; `length(memory_id) = 8`;
  `supersedes_id IS NULL OR length(supersedes_id) = 8`;
  `byte_size >= 0 AND byte_size <= 3904`; `attempts >= 0`.
- Unique `uq_fleet_memory_intents_run_candidate`: `(run_id, candidate_id)`.
- Index `ix_fleet_memory_intents_claim_scan`: `(status, next_attempt_at)`.
- Index `ix_fleet_memory_intents_run`: `(run_id)`.

## SQLite local-development policy

SQLite is supported for deterministic tests and single-machine local
development, not production concurrency. Every Fleet SQLite connection enables
foreign-key enforcement and uses a bounded 5-second busy timeout. File-backed
local databases also use WAL mode. Tests assert `PRAGMA foreign_keys = 1` and
run `PRAGMA foreign_key_check` over valid lineage fixtures.

Production deployments require PostgreSQL. Repository interfaces retain the
same lifecycle contract across both engines; PostgreSQL concurrency and
outbox-worker competition are exercised only by the explicit credentialed
`db` test lane.

The database enforces Turn-to-Run lineage and SandboxBinding workspace lineage.
The binding constraint pairs `(session_id, workspace_id)` with the Session's
same pair, so a binding cannot name an unrelated Workspace. Artifact and Memory
outbox rows retain independently validated Run, Session, User, and Workspace
foreign keys; broader composite lineage is intentionally deferred until a
separate migration can preflight existing production data and prove its
cross-database upgrade path.
