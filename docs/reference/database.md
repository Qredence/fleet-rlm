# Database and persistence

This release starts a new schema baseline at version 1. Existing version-12 databases
are refused at startup; this release does not upgrade or restore them automatically.
Keep the existing database and prior Fleet deployment intact. Use a new empty schema
and a new empty Daytona Volume, then validate the new deployment before directing
clients to it. Existing history remains accessible through the prior deployment.
Do not renumber the old migration ledger or copy rows into the new schema. Rollback
requires the prior deployment, database, and Volume together; the new schema is not
backward compatible.

Fleet requires PostgreSQL for Sessions, Turns, memories and selected replays.
Workspace files live on the Session's Daytona Volume. Configure the
connection with `FLEET_DATABASE_URL`; the default DSN is local PostgreSQL at
`postgresql://postgres:postgres@localhost:5432/fleet`.

`src/fleet_rlm/store/` (`Store`) owns SQL, schema migrations, pooling, and transactions.
`SessionRuntime` owns memory validation, bounded history, idempotency, and Turn-record
policies, using one shared `runtime.store` reference rather than a separate harness.
Session rows are created before lease acquisition; releasing a runtime closes its lease,
not the registry's Store.

At startup, Fleet takes a transaction-scoped advisory lock, reads the
`fleet_lite_schema_migrations` ledger, and applies each pending migration once
(`SCHEMA_VERSION = 1` squashed baseline):

- `fleet_lite_sessions`: session settings, memory summary, turn counter, once-per-Session
  `environment_reported` marker (`sandbox_environment_incomplete` is observational only),
  and `ownership_epoch` / advisory lease metadata. `ownership_epoch` advances whenever
  a process acquires the Session lease so stale owners are fenced by CAS comparison.
- `fleet_lite_sandbox_resources`: normalized epoch-fenced sandbox ownership ledger keyed
  by `(session_id, agent_id)` tracking pending `create`/`stop`/`delete` intent, provider IDs,
  and confirmation state. Resource rows are cleared only after provider confirmation (a
  provider not-found answer counts as confirmation); a definitive create rejection clears its
  pending intent, whereas conflicts, timeouts, rate limits, transport errors, and server
  failures retain it for reconciliation. After an epoch takeover, the stale process fences
  local handles and releases its runtime without provider mutations, while the new owner
  reconciles durable resources before admitting writable work.
- `fleet_lite_turns`: ordered per-session Turn records, unique `(session_id, idempotency_key)`
  intent, status (`running`, `completed`, `failed`, `cancelled`), result JSON, and provenance.
- `fleet_lite_memories`: scoped memories with kind, source digests, review flag, and an
  expression GIN index matching `to_tsvector('english', content)`.
- `fleet_lite_replay_blobs` and `fleet_lite_replay_snapshots`: content-addressed binary blobs
  (`sha256` primary key) referenced by replay manifests so unchanged workspace files are not
  duplicated across Turns.
- `fleet_lite_review_labels`: human curation metadata and optional `structured_expectations`
  JSONB for selected replay Turns, cascading with replay deletion.
- `fleet_lite_leases`: global coordination leases (including `fleet:optimization-global-lease`).
- `fleet_lite_global_settings`: settings that belong to no Session (`improve` scope auto-run
  switch, thresholds, and token budgets).
- `fleet_lite_optimization_runs`: canonical lifecycle ledger for API-owned and standalone CLI
  jobs (`optimize`, `evaluate_rescore`, `evaluate_replay`, `evaluate_synthetic`) keyed by
  `run_id` with unique `request_id` and a unique partial index (`fleet_lite_one_active_optimization_run`)
  permitting at most one globally active `running` or `approving` job.

Future schema changes must add a numbered migration (`version > 1`) rather than editing
released migrations, and bootstrap/version recording run in one transaction under the
migration lock.

To try a branch with a migration against real data without touching the main
schema, set `FLEET_DATABASE_SCHEMA` (for example `fleet_rlm_pr2`) to a new
lowercase name in that process's environment. Fleet creates the schema, applies
every migration inside it, and uses it through `search_path`; the default schema
keeps working against its own tables. Drop the schema when finished. An opt-in
test applies schema bootstrap to a scratch schema on real PostgreSQL: set
`FLEET_TEST_POSTGRES_URL` and run `uv run pytest tests/store/test_store.py -k real_postgres`.

At Store initialization, replay snapshots older than 30 days are deleted; their
curation rows cascade with them, and unreferenced blobs in `fleet_lite_replay_blobs`
are garbage-collected. Since expiry runs at Store initialization, a
snapshot can remain past nominal expiry while the service is stopped. This policy
applies to selected replay payloads, not MLflow trace retention. MLflow
assessments remain as evaluation history, but cannot make an expired replay
eligible for GEPA.
The Lite names keep its tables separate from legacy Fleet tables in an existing
database. `FLEET_DATABASE_SCHEMA` selects the schema; the code fallback is the
compatibility schema `public`, while the checked-in config selects
`fleet_rlm_lite` in the public example. The configured database
must already exist with appropriate permissions.

`fleet_lite_sessions` stores each session's timestamp, memory summary, turn
count, the once-per-Session environment-observation marker, and the advisory lease
epoch/owner/expiry metadata (with pending sandbox resource rows held in
`fleet_lite_sandbox_resources`).
Releasing a runtime keeps the row, so reopening the Session does not repeat the
observation. A Turn's record, memory note, optional replay
snapshot and completion commit in one transaction; workspace file bytes are not
copied to PostgreSQL.
The successful result JSON is also the live/reconnected completion payload and
includes `turn_id` for new Turns. Historical readers obtain a missing `turn_id`
from the row and preserve unknown result fields; no migration or backfill is
needed. See the [result field and legacy inventory](turn-results.md) for commit
readback, uncertain outcomes, copy ownership and rollback.

Structured memories retain their kind, review status, and source digests.
Retrieval uses bounded full-text relevance and recency ranking. Turn
intents retain idempotency keys and terminal outcomes; uncertain running turns
are not automatically replayed. Runtime settings retain selected mode and
dataset revision. A lease row with a 60-second TTL and a fencing epoch prevents two workers from
owning one Session runtime. The shared heartbeat renews it every 20 seconds.
Turn history and status reads use the shared pool and do not take the lease.
A worker that loses the lease evicts the stale runtime and rebuilds it on the
next request once any draining Turn finishes.
Mode and context changes take effect in the live runtime only after their
settings have been saved successfully.

Turn records retain pre-execution SHA-256 values in `context_files` for every
advertised workspace path, not only the selected dataset. Listing, digests,
manifests, and replay capture share the managed roots `files/`, `artifacts/`,
`memory/`, `skills/`, `buffers/`, and `sessions/`. Unmanaged top-level scratch
files and checkouts are excluded without deleting or moving their bytes.
Symbolic links are not traversed; a symbolic-link managed root, malformed
managed path, or invalid inventory response fails the Turn before success
persistence. A failed commit does not roll back Volume bytes, and its root
trace reports failure rather than a completed answer.
When a caller explicitly
sets `record_replay=true`, Fleet writes the complete bounded input snapshot to
`fleet_lite_replay_snapshots`, keyed by Session and Turn and protected by a
content digest. It commits only with successful Turn completion; cascade deletion
follows the parent Turn. GEPA checks the trace reference and restores the stored
bytes in its isolated workspace rather than reading the current workspace. Ordinary
Turns retain hashes only and cannot enter GEPA without a selected snapshot.

For Lakebase, use the direct PostgreSQL endpoint with TLS for `FLEET_DATABASE_URL`.
Fleet uses pooled TTL-row leases for Sessions and optimization owners. Lease renewal
shares the application pool; no dedicated connection is held for a lease. Use the
direct endpoint for migration advisory locks and runtime transactions.
established connection continues after its token expires. Databricks documents
a 24-hour idle timeout and a three-day maximum connection lifetime; test Fleet's
reconnect path against these service limits. The process probes each lease
connection periodically; a failed probe cancels that session's sandbox work and fences the runtime so it
cannot accept another Turn. A server-side idle timeout or network partition may
delay failure detection, so verify behavior with an authorized Lakebase
fault-injection canary before relying on a specific detection time. When
`FLEET_LAKEBASE_ENDPOINT` enables OAuth, Fleet mints fresh credentials for new
physical pool connections and dedicated leases using Databricks SDK
authentication. OAuth is not compatible with Lakebase's built-in PgBouncer
transaction pooler. See the [Lakebase authentication guidance](https://docs.databricks.com/gcp/en/oltp/projects/authentication)
and [pooling constraints](https://docs.databricks.com/gcp/en/oltp/projects/connection-pooling)
before changing the role's authentication method.

When `FLEET_DATABASE_MIGRATION_URL` is configured, Fleet creates the selected
schema and applies migrations through that connection; runtime queries and
session leases continue to use `FLEET_DATABASE_URL` with that schema as the
connection search path. The separate migration pool closes after startup
migrations, before the API begins serving. Provision the migration role with schema creation and
DDL rights, then grant the runtime role schema usage and required table
privileges. Without a separate migration DSN, the runtime role must also be able
to create the schema and apply migrations. Keep the old database intact until
the new database has been bootstrapped, restored, and accepted, and document the
selected database and rollback DSN outside committed configuration.

Deployments may use one role for migrations and runtime queries or separate
roles. For separate roles, grant the runtime role database `CONNECT`, schema
`USAGE`, and table `SELECT`, `INSERT`, `UPDATE`, and `DELETE`. Configure default
table privileges for future migrations. Role names and authentication methods
are deployment choices; keep both DSNs in ignored secret configuration.

Daytona Volume bytes are not part of a database transaction. Restarting
recovers Turns and settings from PostgreSQL and files from the Volume; it does
not restore Python process memory. A new schema must not reuse a Volume that
already holds Session subtrees: a newly inserted Session is recorded
`known_empty` without listing that Volume.

New optimization status is stored in PostgreSQL; candidate manifests, reports,
and GEPA working files remain in the configured artifact directory, while MLflow
stores optimization traces and evidence. The installed program is the single file
`<compiled_program_dir>/fleet.json`; there is no per-Session artifact or selection
setting, and the dataset spans every Session's approved labels. API restart marks abandoned managed workers
interrupted and does not rerun provider work; a later explicit review reconciles
an interrupted approval against the active candidate digests.

Each API holds a pooled TTL owner lease through worker startup and
draining. History reads preserve runs owned by another live API. Abandoned
managed workers receive a cooperative stop request while holding the global
optimization lease; their rows become interrupted only after it is released.
Standalone optimization reconciles abandoned running rows under that global
lease before admitting a new run, so a crashed CLI does not require an API
history read to unblock the next search. Ctrl+C during search or validation
records cancellation and clears candidate eligibility before exiting.

The default URL includes a development username and password; replace it for
any shared or production environment. Do not include database secrets in a
committed file. Live database behavior is not established by the local CI
quality gate.
