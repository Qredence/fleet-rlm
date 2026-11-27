# Harden Fleet’s Neon branches and keep local telemetry optional

Review one slice per diff. Record its revision, exact commands, results and outstanding live checks before starting the next slice. Fleet uses the local Docker MLflow tracker.

## Risks

1. A sleeping or unreachable tracker can stall FastAPI startup and consume turn deadlines. Fleet owns admission and persistence in Postgres. MLflow owns optional observations. A telemetry error must never replay a model call or obscure cancellation.
2. Branch copies inherit data and privileges. Separate databases share branch lifecycle and compute. A runtime role that owns tables can still perform DDL. Concurrent optimizer jobs can exhaust Fleet’s connection budget even with PgBouncer.

## 1. Remove tracking from Fleet availability

**Implementation prompt.** Work only on Fleet’s telemetry client in the installed fleet_rlm package and its nearest tests. Keep experiment setup off the serving loop, bound request timeout and retries, allow one initializer per worker and retry failed setup. Withhold tracing until initialization succeeds. Preserve healthy run correlation, observable degradation and bounded shutdown. Keep span failures outside model execution and terminal persistence.

Run from the repository root. The placeholder host selects the offline suite in Fleet’s locking tests. Require a committed answer, one execution and a terminal SSE frame with a refused or stalled tracker. Repeat with a trace operation failing during the turn. Verify healthy trace correlation separately.

```sh
unset DAYTONA_API_URL DAYTONA_V2_API_URL
export FLEET_DATABASE_URL=postgresql://ep-example.invalid/offline
export FLEET_DATABASE_DIRECT_URL="$FLEET_DATABASE_URL"
(cd backend && uv run --locked pytest -q tests/test_phase3_api.py -k 'trac or mlflow or disconnect')
make check-backend
```

**Pre-Slice 2 review, November 27, 2026.** Reviewed the working-tree diff based on `9636d570f`. The initial timeout released the FastAPI lifespan, but Python’s default executor could still wait for a blocked SDK thread when closing the event loop. Fleet now uses daemon threads for experiment lookup and final flush. Two regression cases keep each SDK operation blocked and require the worker’s event loop to exit before releasing it. This verifies Fleet-owned shutdown.

The review also found unguarded MLflow context entry, context exit and DSPy callback construction. Fault injection now checks that the original turn cancellation survives context errors and a callback setup error still permits one completed chat turn. With a refused or stalled HTTP tracker, the real FastAPI lifespan served readiness HTTP 200, the offline store recorded one completion, and the client received its answer and `[DONE]`.

Both commands above passed on the reviewed diff. The focused gate passed 16 tests. The full backend gate passed 293 with 13 skipped under the offline configuration. A separate MLflow 3.18 SQLite canary retrieved one trace from the selected experiment with the expected Fleet run ID. The live Neon rehearsal is recorded below.

## 2. Rehearse Neon role separation

On November 27, 2026 the Neon plugin listed an unprotected default main, a canary cloned with parent data and a schema-only review branch due to expire November 28. Inventory each branch’s consumer before changing protection or retiring branches. Use schema-only disposable branches for migration tests and a sanitized baseline for optimizer work. Keep Fleet on native Postgres. Adding the Neon Data API duplicates the HTTP surface already owned by FastAPI.

**Implementation prompt.** Write role provisioning and a disposable-branch rehearsal. Provision ordinary SQL login roles. Let app_migrator own Fleet’s schema and objects and perform migration backfills. Grant app_runtime CONNECT, schema USAGE and required table/sequence DML for existing and future objects created by app_migrator. Remove inherited owner membership and PUBLIC CREATE/TEMP privileges where applicable. Use the pooled hostname for runtime and the direct hostname for Alembic with NullPool. Both can use port 5432. [Neon connection pooling](https://neon.com/docs/connect/connection-pooling) and [branch lifecycle](https://neon.com/docs/introduction/branching). Serialize migrations per branch. Budget workers × (pool size + overflow), optimizer processes and migration headroom against actual compute limits. Collect peak active sessions, pool wait and query latency under concurrent turn/optimization load before proposing indexes.

### Slice 2 review gate

On November 27, 2026 the Neon plugin created [fleet-role-rehearsal-cacb2dca](https://console.neon.tech/app/projects/polished-cherry-13944788/branches/br-misty-king-bac8yhbr) in the existing project. Its ID is `br-misty-king-bac8yhbr` and its expiry is November 28 at 17:44 UTC. The project had reached its schema-only root quota. Neon also disallows children of expiring parents. The rehearsal used a child of the existing schema-only reference `br-flat-rice-baxyhhya`. A preflight count on the parent found one Alembic revision row and zero application rows. Only the child received writes.

| Gate | Observed on the disposable child |
| --- | --- |
| Direct login and real schema upgrade | `app_migrator` ran `alembic upgrade head` from `0003_fk_and_query_indexes` through `0011_session_reasoning_override`. The Neon plugin independently confirmed the revision and ownership of the public schema and all 11 Fleet tables, including the revision table. |
| Pooled runtime writes | Both session and current user were `app_runtime`. A row inserted before the upgrade survived. SELECT, INSERT, UPDATE and DELETE succeeded on the existing sessions table and a new migrator-owned identity table. Explicit sequence allocation succeeded. |
| DDL and migration metadata | Permanent CREATE TABLE, ALTER TABLE, DROP TABLE, CREATE TEMP TABLE and CREATE SCHEMA each raised SQLSTATE `42501`. Updating the revision table also returned `42501`. |
| Application concurrency | The existing root-lock test passed using the pooled runtime credentials. Two concurrent contenders produced one winner and one conflict. |

**Reproduce on a fresh disposable branch.** From the repository root, with the Neon CLI authenticated to the Fleet project, run:

```sh
(cd backend && PGSSLROOTCERT=/etc/ssl/cert.pem uv run --locked python ../scripts/neon/rehearse_roles.py)
```

The [rehearsal runner](../../scripts/neon/rehearse_roles.py) creates a 24-hour child of the reference. It verifies the branch metadata, both endpoint types and the SQL identities, then runs [the provisioning transaction](../../scripts/neon/fleet_roles.sql), Alembic, permission probes and the application contention test. Use the path to your system CA bundle if it differs. The observed run used the plugin-created child and existing loopback forwards because outbound port 5432 timed out. TLS still verified the Neon hostname.

```sh
(
  cd backend
  export PGSSLROOTCERT=/etc/ssl/cert.pem REHEARSAL_DIRECT_PORT=15433 REHEARSAL_POOL_PORT=15432
  uv run --locked python ../scripts/neon/rehearse_roles.py br-misty-king-bac8yhbr
)
```

The runner provisions random role passwords over verified TLS and retrieves each role’s URL using the authenticated Neon CLI. A rerun on the same child reuses the existing credentials. If the child is already at Alembic head, the runner marks the migration and row-survival checks as skipped and still exercises permissions and root contention. On a fresh child, expected output includes the old and new revisions, six `42501` results, and `1 passed` for the contention test. A failure exits nonzero. The explicit database grants give the migrator TEMP for the temporary helper function in revision 0004. The runtime has DML on application tables and no write grant on migration metadata.

**Review boundary.** This establishes permissions on `neondb` in the rehearsal child. Main branch protection, application credential rollout and representative capacity or latency measurements remain separate decisions. The Neon plugin reported `max_connections = 450` on the test compute. The two-contender check establishes correctness under contention. Throughput remains unmeasured.
