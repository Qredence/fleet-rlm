# Database and persistence

Fleet requires PostgreSQL for Session history and VFS content. Configure the
connection with `FLEET_DATABASE_URL`; the default DSN is local PostgreSQL at
`postgresql://postgres:postgres@localhost:5432/fleet`.

At startup, Fleet runs `CREATE TABLE IF NOT EXISTS` for the Lite-specific
`fleet_lite_sessions` and `fleet_lite_vfs_files` tables. The Lite names keep its
schema separate from legacy Fleet tables in an existing database. It does not
use Alembic or a migration workflow. PostgreSQL itself must be installed or
provisioned separately, and the configured database must already exist with
appropriate permissions.

`fleet_lite_sessions` stores each session's timestamp, memory summary, and
bounded turn history as JSONB. `fleet_lite_vfs_files` stores session-scoped
relative paths, content, SHA-256 values, metadata, and update times. Workspace
contents are restored from and synchronized to this VFS through the Daytona
sandbox manager.

The default URL includes a development username and password; replace it for
any shared or production environment. Do not include database secrets in a
committed file. Live database behavior is not established by the local CI
quality gate.
