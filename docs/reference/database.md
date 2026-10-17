# Database and persistence

Fleet requires PostgreSQL for Session history and VFS content. Configure the
connection with `FLEET_DATABASE_URL`; the default DSN is local PostgreSQL at
`postgresql://postgres:postgres@localhost:5432/fleet`.

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
The migration role owns the change; existing runtime DML grants apply.
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

`fleet_lite_sessions` stores each session's timestamp, memory summary, and
bounded turn history as JSONB. `fleet_lite_vfs_files` stores session-scoped
relative paths, content, SHA-256 values, metadata, and update times. Workspace
contents are restored from and synchronized to this VFS through the Daytona
sandbox manager. A complete bounded scan must succeed before deletions are
committed; a failed transfer cannot mark unvisited files deleted. File bytes
and successful turn history commit in one transaction. Limits are 25 MB per
file, 100 MB per workspace sync, and 5,000 files.

Structured memories retain their kind, review status, and source digests.
Retrieval uses bounded full-text relevance and recency ranking; stale source
hashes are reported rather than treated as verified current evidence. Turn
intents retain idempotency keys and terminal outcomes; uncertain running turns
are not automatically replayed. Runtime settings retain selected mode and
dataset revision. A session advisory lock prevents two workers from owning
one session runtime at once and holds one database connection until close.
Mode and context changes take effect in the live runtime only after their
settings have been saved successfully.

Turn records retain pre-execution SHA-256 values in `context_files` for every
advertised workspace path, not only the selected dataset. When a caller explicitly
sets `record_replay=true`, Fleet writes the complete bounded input snapshot to
`fleet_lite_replay_snapshots`, keyed by Session and Turn and protected by a
content digest. It commits only with successful Turn completion; cascade deletion
follows the parent Turn. GEPA checks the trace reference and restores the stored
bytes in its isolated workspace rather than reading the current VFS. Ordinary
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

Daytona Volume bytes are a working copy, not a database transaction. Recovery
restores files and metadata; it does not restore Python process memory.

The default URL includes a development username and password; replace it for
any shared or production environment. Do not include database secrets in a
committed file. Live database behavior is not established by the local CI
quality gate.
