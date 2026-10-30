# Database and persistence

Fleet requires PostgreSQL for Sessions, Turns, memories and selected replays.
Workspace files live on the Session's Daytona Volume. Configure the
connection with `FLEET_DATABASE_URL`; the default DSN is local PostgreSQL at
`postgresql://postgres:postgres@localhost:5432/fleet`.

`store.py` owns SQL, migrations, pooling, and transactions. `SessionRuntime`
owns memory validation, bounded history, idempotency, and Turn-record policies,
using one shared `runtime.store` reference rather than a separate harness. Session
rows are created before lease acquisition; releasing a runtime closes its lease,
not the registry's Store. The consolidation changes no SQL or schema version.

At startup, Fleet takes a transaction-scoped advisory lock, reads the
`fleet_lite_schema_migrations` ledger, and applies each pending migration once.
The initial migration creates additive tables for
`fleet_lite_sessions`, `fleet_lite_vfs_files`, `fleet_lite_memories`,
`fleet_lite_turns`, `fleet_lite_replay_snapshots`, and
`fleet_lite_runtime_settings`. The ledger records the applied schema version;
bootstrap and version recording run in one transaction. Future schema changes
must add a numbered migration rather than edit an already released migration.
Migration 2 adds `fleet_lite_review_labels`, which stores human curation metadata
only for an existing selected replay and cascades with that replay's deletion.
Migration 3 adds optional `structured_expectations` JSONB with an empty-object
default. It stores artifact/delegation gold without changing replay snapshots.
Migration 4 renames `fleet_lite_vfs_files` to `fleet_lite_vfs_files_retired`.
Fleet no longer reads or writes it; the rows stay for manual recovery and the
table can be dropped by an operator.
Migration 5 adds `fleet_lite_sessions.environment_reported`, the durable
once-per-Session marker for the soft `sandbox_environment_incomplete`
observation. Existing rows default to unreported; no backfill is performed.
The migration role owns the change; existing runtime DML grants apply.
Migration 6 adds `fleet_lite_optimization_runs`, the canonical lifecycle ledger
for API-owned and standalone CLI optimization runs. A unique partial index permits
at most one globally active run or approval; there is no job queue. The record
stores request idempotency, validated options, owner, managed-worker marker,
status/stage, cooperative stop intent, and run metadata. Database owner/status
checks fence stale workers from publishing lifecycle updates.
Migration 7 adds an expression GIN index matching memory search's
`to_tsvector('english', content)` predicate. PostgreSQL chooses whether to use
it; it does not eliminate ranking work. The initial index build runs under the
existing migration transaction/lock and adds index-maintenance cost to writes.
At Store initialization, replay snapshots older than 30 days are deleted; their
curation rows cascade with them. Since expiry runs at Store initialization, a
snapshot can remain past nominal expiry while the service is stopped. This policy
applies to selected replay payloads, not MLflow trace retention. MLflow
assessments remain as evaluation history, but cannot make an expired replay
eligible for GEPA.
The Lite names keep its tables separate from legacy Fleet tables in an existing
database. `FLEET_DATABASE_SCHEMA` selects the schema; the code fallback is the
compatibility schema `public`, while the checked-in config selects
`fleet_rlm_lite` for the fresh application database. The configured database
must already exist with appropriate permissions.

`fleet_lite_sessions` stores each session's timestamp, memory summary, bounded
turn history as JSONB, and the once-per-Session environment-observation marker.
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
dataset revision. A session advisory lock prevents two workers from owning
one session runtime at once and holds one database connection until close.
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
Fleet holds a session-level PostgreSQL advisory lock on a dedicated connection;
Lakebase's [transaction pooler](https://docs.databricks.com/gcp/en/oltp/projects/connection-pooling)
does not support this lock lifetime. Keep
application-side pooling for ordinary queries and use a direct connection for
each session lease. Lakebase OAuth tokens are checked at login, and an
established connection continues after its token expires. Databricks documents
a 24-hour idle timeout and a three-day maximum connection lifetime; test Fleet's
reconnect path against these service limits. The process probes each lease
connection periodically; a failed probe cancels that session's sandbox work and fences the runtime so it
cannot accept another Turn. A server-side idle timeout or network partition may
delay failure detection, so verify behavior with an authorized Lakebase
fault-injection canary before relying on a specific detection time. If Lakebase
OAuth is adopted, mint fresh credentials for new physical pool connections and
dedicated leases; the current password-based `fleet_app` role does not implement
credential refresh. OAuth is not compatible with Lakebase's built-in PgBouncer
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

The fresh Lakebase database uses native-password `fleet_migrator` for DDL and
`fleet_app` for runtime queries. `fleet_migrator` owns the `fleet_rlm_lite`
schema and Fleet tables. `fleet_app` has database `CONNECT`, schema `USAGE`, and
table `SELECT`, `INSERT`, `UPDATE`, and `DELETE`; it has no schema `CREATE` grant
or membership in `fleet_migrator`. Default table privileges give it the same
DML rights on tables created by future migrations. Both DSNs stay in ignored
local/deployment secret configuration. The shared Lakebase endpoint's scaling
and suspend settings are unchanged.

Daytona Volume bytes are not part of a database transaction. Restarting
recovers Turns and settings from PostgreSQL and files from the Volume; it does
not restore Python process memory.

New optimization status is stored in PostgreSQL; candidate manifests, reports,
and GEPA working files remain in the configured artifact directory, while MLflow
stores optimization traces and evidence. Existing per-Session JSON run records
are imported idempotently at startup and before history reads. They are retained
as read-only migration sources. API restart marks abandoned managed workers
interrupted and does not rerun provider work; a later explicit review reconciles
an interrupted approval against the active candidate digests.

Each API holds a dedicated advisory owner lease through worker startup and
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
