# Harden Fleet’s Neon branches and deploy shared MLflow

Review one slice per diff. Record its revision, exact commands, results and outstanding live checks before starting the next slice. An unsuccessful gate keeps the existing writer authoritative.

## Risks in deployment order

1. A sleeping or unreachable tracker can stall FastAPI startup and consume turn deadlines. Fleet owns admission and persistence in Postgres. MLflow owns optional observations. A telemetry error must never replay a model call or obscure cancellation.
2. A SQLite copy taken during active writes can omit committed WAL pages. Metadata can reference files written later, absolute Docker paths, or missing artifacts. Sending old and new workers to different stores silently forks history.
3. Branch copies inherit data and privileges. Separate databases share branch lifecycle and compute. A runtime role that owns tables can still perform DDL. Concurrent optimizer jobs can exhaust Fleet’s connection budget even with PgBouncer.
4. Access login redirects can look like HTTP 200 HTML after redirect following. Generic HTTP headers can carry service credentials to redirected artifact hosts. Container-local files and subprocesses can disappear on sleep or restart.

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

**Pre-Slice 2 review, November 27, 2026.** Reviewed the working-tree diff based on `9636d570f`. The initial timeout released the FastAPI lifespan, but Python’s default executor could still wait for a blocked SDK thread when closing the event loop. Fleet now uses daemon threads for experiment lookup and final flush. Two regression cases keep each SDK operation blocked and require the worker’s event loop to exit before releasing it. This verifies Fleet-owned shutdown. Container termination with MLflow’s own exporter queues pending belongs to the deployment rehearsal.

The review also found unguarded MLflow context entry, context exit and DSPy callback construction. Fault injection now checks that the original turn cancellation survives context errors and a callback setup error still permits one completed chat turn. With a refused or stalled HTTP tracker, the real FastAPI lifespan served readiness HTTP 200, the offline store recorded one completion, and the client received its answer and `[DONE]`.

Both commands above passed on the reviewed diff. The focused gate passed 16 tests. The full backend gate passed 293 with 13 skipped under the offline configuration. A separate MLflow 3.18 SQLite canary retrieved one trace from the selected experiment with the expected Fleet run ID. Live shared-tracker Browser acceptance and the Neon permissions rehearsal remain pending.

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

The runner provisions random role passwords over verified TLS and retrieves each role’s URL using the authenticated Neon CLI. A rerun on the same child reuses the existing credentials. Expected output includes the old and new revisions, six `42501` results, and `1 passed` for the contention test. A failure exits nonzero. The explicit database grants give the migrator TEMP for the temporary helper function in revision 0004. The runtime has DML on application tables and no write grant on migration metadata.

**Review boundary.** This establishes permissions on `neondb` in the rehearsal child. Main branch protection, application credential rollout and representative capacity or latency measurements remain separate decisions. The Neon plugin reported `max_connections = 450` on the test compute. The two-contender check establishes correctness under contention. Throughput remains unmeasured.

## 3. Deploy and qualify one shared tracking endpoint

**Implementation prompt.** Deploy a staging tracking endpoint and exercise it with Fleet’s actual MLflow client. Use a dedicated Neon project for shared MLflow so Fleet branch restores and optimizer clones exclude tracking metadata. A separate database in Fleet’s existing project is a lower-overhead option if shared restore and access boundaries are acceptable and a concurrent load test demonstrates sufficient compute headroom. Keep optimization compute with Fleet. The container serves MLflow only. Use a single named container behind the platform’s minimal Worker ingress, private R2 with proxied artifacts and a pinned server image matching Fleet. Use the Cloudflare plugin to inventory Container applications, Access applications/policies/service tokens and R2 buckets, then apply the reviewed configuration and read back bindings, public routes and Access coverage. The plugin’s OpenAPI search exposes account-level containers/applications, access/service_tokens and r2/buckets operations. [Container lifecycle](https://developers.cloudflare.com/containers/examples/status-hooks/) and [Access service tokens](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/).

Use a Service Auth policy with machine-denial responses. Inject CF-Access-Client-Id and CF-Access-Client-Secret server-side only for the exact HTTPS tracking origin. Disable cross-origin redirects for authenticated requests. Keep UI sign-in under a separate human policy. Browser traffic goes through Fleet, so opening MLflow CORS buys nothing. For acceptance use a protected CI secret file containing curl header directives, mode 0600, and disable shell tracing.

```bash
set -eu
endpoint="${MLFLOW_ORIGIN:?}/api/2.0/mlflow/experiments/search"
body=$(mktemp)
trap 'rm -f "$body"' EXIT
probe() { curl -sS --max-time 60 -o "$body" -w '%{http_code}' "$@" -H 'Content-Type: application/json' -d '{"max_results":1}' "$endpoint"; }
case "$(probe)" in 401|403) ;; *) exit 1;; esac
test "$(probe --config "${CF_TOKEN_CURL_CONFIG:?}")" = 200
jq -e '.experiments | type == "array"' "$body"
```

Repeat after idle sleep and forced restart. Reject a revoked token, verify alternate hostnames cannot bypass Access, and use a controlled redirect receiver to assert zero credential headers. Measure time to first authenticated JSON response. While the tracker wakes, run a Fleet turn and a long optimizer job. Confirm one committed answer and one terminal job with a downloadable artifact matching its checksum.

## 4. Freeze SQLite and switch writers

**Implementation prompt.** Produce the read-only archive, rehearse restoration and perform the writer switch only after the restore passes. Archive historical runs in place and start a fresh experiment on Neon/R2 unless searchable historical migration becomes a separate requirement. The reported 21,618 traces and 2,101 artifact files are comparison points. Recount at freeze. Stop new admissions and schedules, drain active turns/jobs and asynchronous exporters, then stop every tracking writer including local CLIs. Create a consistent SQLite backup plus the entire artifact tree. Record source paths and MLflow image version so stored URIs resolve in a read-only restore. Keep the original volume.

```bash
set -eu
test "$(sqlite3 -readonly "${ARCHIVE_DB:?}" 'PRAGMA integrity_check')" = ok
sqlite3 -readonly "$ARCHIVE_DB" 'SELECT count(*) AS traces FROM trace_info; SELECT count(*) AS runs FROM runs;'
(cd "${ARCHIVE_ROOT:?}" && shasum -a 256 -c "${ABSOLUTE_MANIFEST_PATH:?}")
test "$(curl -fsS "${FLEET_ORIGIN:?}/readyz" | jq -r .status)" = ok
```

Generate the relative-path manifest at freeze over the database snapshot and each artifact, store it outside the tree and compare a fresh file inventory on restore. Verify one old trace, run and downloaded artifact by stable ID and hash. Switch every writer together. Submit one identified Browser chat, match the persisted answer and Fleet run ID to its trace, then download a new optimizer artifact after container restart. If rollback follows any new write, freeze again, retain both stores and record the boundary run IDs before switching writers. Never merge histories by overwriting either database.
