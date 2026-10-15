# Database and persistence

Fleet requires PostgreSQL for Session history and VFS content. Configure the
connection with `FLEET_DATABASE_URL`; the default DSN is local PostgreSQL at
`postgresql://postgres:postgres@localhost:5432/fleet`.

At startup, Fleet runs additive `CREATE TABLE IF NOT EXISTS` statements for
`fleet_lite_sessions`, `fleet_lite_vfs_files`, `fleet_lite_memories`,
`fleet_lite_turns`, and `fleet_lite_runtime_settings`. The Lite names keep its
schema separate from legacy Fleet tables in an existing database. It does not
use Alembic or a migration workflow. PostgreSQL itself must be installed or
provisioned separately, and the configured database must already exist with
appropriate permissions.

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

Daytona Volume bytes are a working copy, not a database transaction. Recovery
restores files and metadata; it does not restore Python process memory.

The default URL includes a development username and password; replace it for
any shared or production environment. Do not include database secrets in a
committed file. Live database behavior is not established by the local CI
quality gate.
