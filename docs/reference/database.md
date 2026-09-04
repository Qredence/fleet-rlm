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
`fleet_turns`, `fleet_runs`, `fleet_sandbox_bindings`, `fleet_attachments`, `fleet_artifacts`, and
`fleet_skills`. SQLAlchemy models live in `fleet_rlm.persistence.models`.

Production startup assumes migrations have already run. Explicit SQLite
test/offline helpers may call `create_tables`; all other environments must use
Alembic.

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
