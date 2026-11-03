# Phase 02: setup and readiness for memory storage

Status: deferred, 2026-11-03. Not implemented by the scope-reduction cleanup. Depends on
[Phase 01](memory-01-scope-reduction.md). See the [program index](memory-program.md).

## Purpose

MEM-1 and MEM-2 ask for two things Fleet cannot currently show an operator: that
repeated initialization is safe for the existing data, and that the memory
database is usable, with reasons when it is not.

After scope reduction, the existing read-only setup probe remains. It checks
connectivity and schema version, but does not expose a database-only CLI or a
complete privilege assessment. The design below is future work, not behavior
provided by the cleanup.

## Requirements

| ID | Acceptance criterion |
| --- | --- |
| MEM-1 | Existing additive initialization succeeds twice without losing data. |
| MEM-2 | Readiness identifies database, schema, version, and required access without models or Daytona. |

Both are about the configured memory database only. Neither promises provider
readiness, sandbox availability, or model access.

## Current behavior and its gaps

`SetupService.check_database` opens the configured connections in read-only
transactions, verifies connectivity, checks for Fleet tables, and reads the
migration ledger. It refuses versions newer than `SCHEMA_VERSION`.

Initialization remains part of API startup. A database-only CLI and detailed
privilege reporting are deferred. The experimental managed-memory preflight and
outbox sequence checks have been removed.

## Design

### 1. Read-only probe stays read-only

Keep `check_database` read-only. It must not create the schema, apply a
migration, or write a row. The proposed queries are:

- `current_database()`, `current_user`, and whether the role may create objects;
- schema existence, owner, `USAGE`, and `CREATE`;
- per-table `SELECT`/`INSERT`/`UPDATE`/`DELETE` for `fleet_lite_*` tables;
- `MAX(version)` from the migration ledger, when the ledger exists.

Do not probe privileges on inert managed-memory tables or their sequence.
Check ownership only for objects migrations actually alter; schema ownership
is not required when the role has the needed schema grants and table ownership.
Do not add a write or a lock.

Reuse the existing connection seam so the probe is
testable without a live database. The existing tests already inject a fake
connection; keep that seam.

### 2. Database-only initialize command

Add one CLI command:

```
uv run fleet db check   # read-only probe; prints the same facts the API uses
uv run fleet db init    # apply pending migrations; idempotent; then probe
```

Both resolve settings from the same configuration the API uses, including
`FLEET_DATABASE_URL`, the optional migration DSN, the schema, and existing Databricks SDK authentication
when Lakebase OAuth is enabled. Neither requires Databricks managed memory, a model,
or Daytona.

`db init` uses the existing path: `Store.open(...)`, `Store.initialize()`,
`Store.close_migration_pool()`, then a read-only probe. It must not start the API,
acquire a Session, or create a sandbox. `Store.initialize` already serializes on
an advisory lock and records each version once, so repeating it is safe by
construction; the command makes that observable rather than adding new
idempotency machinery.

Exit codes: `0` ready, `1` blocked, `2` configuration invalid. Messages name the
failing fact and the remedy, and never include a DSN, password, or token. The
existing setup probe returns the same structure so the API and CLI cannot drift.

### 3. Report shape

One structure, used by both surfaces:

```json
{
  "status": "ready",
  "database": "fleet",
  "role": "fleet_runtime",
  "schema": "fleet_rlm_lite",
  "schema_exists": true,
  "schema_version": 11,
  "code_schema_version": 11,
  "privileges": {
    "schema_usage": true,
    "schema_create": true,
    "table_dml": true,
    "migration_ddl": true
  },
  "schema_initialized": true,
  "message": "Database identity and Fleet privileges verified."
}
```

Rules:

- `status` is `ready` or `blocked`. Warnings are not used: a storage fact is
  either satisfied or it is not.
- `blocked` always includes a specific `message` and the facts that produced it.
- A recorded version newer than `code_schema_version` is `blocked` and refuses
  initialization. Do not attempt a downgrade.
- A missing schema or ledger is not `blocked` before `db init`; it reports
  `schema_initialized: false` and what initialization would do. `db check` is
  allowed to succeed on an empty database.
- `db init` then re-probes and reports the post-initialization state.

### 4. Separate from cross-service setup readiness

The setup surface may project this result into its `database` section, but the
database verdict is computed by the storage probe alone. A blocked Daytona or
MLflow check must not make memory storage look unusable, and a blocked database
must not be reported as a model problem.

This is a reporting separation, not a new endpoint. Phase 04's draft route does
not require setup readiness.

## Verification

| Case | Expected |
| --- | --- |
| Fresh empty database | `db check` reports `schema_initialized: false`, `ready`. `db init` creates the schema and tables once. |
| `db init` twice | Second run applies nothing, preserves rows written between runs, and reports the same version. |
| Database at an older version | `db init` applies only pending migrations in order. |
| Database newer than code | `blocked`, refuses to initialize, message names both versions. |
| Role without `USAGE` | `blocked`, message names the schema and the missing privilege. |
| Role without table DML | `blocked`, message names DML. |
| Missing migration `CREATE` | `blocked`, message names the migration role. |
| Wrong database selected by the DSN | `blocked`, message compares the connected and selected database names. |
| No Databricks, model, or Daytona configuration | Both commands still work against a local PostgreSQL. |
| Invalid DSN or schema name | Exit code 2, sanitized message, no credential echoed. |

Coverage is offline with a fake connection, plus one separately authorized live
canary. The live canary runs `db init` twice against the selected database and
records the version before and after.

## Out of scope

- No automatic migration on API startup change. `Store.initialize` keeps its
  current role.
- No destructive reset, downgrade, or repair command.
- No schema creation through the runtime role when a separate migration role is
  configured.
- No model or Daytona probing.

## Risks

| Risk | Mitigation |
| --- | --- |
| A second initialization path diverges from startup. | `db init` calls `Store.initialize` rather than reimplementing it. |
| An operator reads `db check` success as full setup readiness. | The report is storage-only, and the setup projection keeps its own section verdict. |
| Running migrations outside the service is unexpected. | The command is explicit, prints the target database and schema, and never runs automatically. |
| The probe's privilege matrix is over-trusted on a managed provider. | The live canary is the evidence; the probe reports what PostgreSQL says, not a guarantee. |
