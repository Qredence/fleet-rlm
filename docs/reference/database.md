# Database and persistence

Fleet uses PostgreSQL for durable application state, a Daytona Volume for the mutable
execution workspace, and optional private S3-compatible storage for published files.
MLflow owns its own database and artifact store. These are separate transaction domains.

## Neon connection

Set `FLEET_DATABASE_URL`, `FLEET_DATABASE_MIGRATION_URL` (optional separate DDL role),
and `FLEET_DATABASE_SCHEMA` (default `fleet`). Use direct PostgreSQL endpoints with TLS
on Neon. Fleet retains Psycopg pooling; startup `search_path` requires a direct endpoint.
Runtime and optional migration DSNs must address the same Neon host, port and database.

Schema version 2 preserves released migration 1 and applies numbered migration 2.
The runner serializes with the original transaction advisory-lock identity, hands the
legacy ledger to `schema_migrations`, and rejects conflicting ledgers or newer versions.
Existing version-1 schemas upgrade in place; version-12 databases remain unsupported.
Fresh installations apply both migrations. Do not edit released migrations.

Migration roles need schema creation and DDL privileges. Runtime roles need database
CONNECT, schema USAGE, table SELECT/INSERT/UPDATE/DELETE, and sequence USAGE/SELECT.
Configure default privileges for future tables and sequences. The DDL pool closes after
startup. Keep credentials in ignored environment files; `/config` excludes secrets.

## Tables and ownership

| Table | Responsibility |
| --- | --- |
| `sessions` | Settings, memory summary, counters, environment marker, ownership epoch, TTL lease and inventory freshness |
| `turns` | Task intent, unique session idempotency key, sequence, status, result/record, route and regeneration source |
| `messages` | Stable user/assistant messages owned by a session and turn, with unique positions and roles per turn |
| `notes` | Existing session-scoped memories: content, kind, source digests, reviewed flag, and full-text index |
| `files` | Immutable published uploads/outputs: ID, session, optional turn, name, media type, size, digest, object locator and workspace path |
| `message_files` | Same-session message/file foreign keys |
| `workspace_files` | Cached, explicitly refreshed inventory of managed workspace paths |
| `sandbox_resources` | Epoch-fenced provider allocation and cleanup intents; rows remain until provider confirmation |
| `replay_snapshots` | Immutable selected replay manifests and digests, owned by turns |
| `replay_blobs` | Content-addressed replay bytes, retained in PostgreSQL |
| `review_labels` | Current reviewed expectations, cascading with replay-snapshot deletion |
| `jobs` | Evaluation/optimization lifecycle, worker ownership, cancellation and promotion evidence |
| `leases` | Global TTL coordination leases |
| `global_settings` | Settings outside individual sessions |
| `schema_migrations` | Applied version ledger |

The turn primary key remains `(session_id, turn_id)`. A turn has one user message and
at most one successful assistant message. Stable submission IDs also serve as retry
keys. Regeneration creates a new turn linked to its source within the same session;
completed attempts remain in context. Failed/cancelled attempts retain visible user
messages and terminal status, but stay outside completed-turn context.

A successful assistant message, result/record, memory update, optional selected replay
and published-file references commit together under the session epoch fence. Replays
cannot restore live interpreter state. Results retain their existing extensible JSON
contract and trace/provenance fields. The jobs partial unique index continues to admit
only one globally running/approving job.

At initialization, bounded cleanup prunes unlabelled replay snapshots older than 30 days
and garbage-collects unreferenced blobs. Any labelled replay is retained. Selected
snapshots remain required for GEPA; MLflow traces alone do not establish eligibility.

## Published files and workspace

Configure `FLEET_STORAGE_ENDPOINT`, `FLEET_STORAGE_REGION`, `FLEET_STORAGE_BUCKET`,
`FLEET_STORAGE_ACCESS_KEY`, and `FLEET_STORAGE_SECRET_KEY` for a private bucket.
The host performs S3 operations using SigV4/path-style requests. Credentials never enter
Daytona or the browser. Rows store object keys, not expiring URLs.

Uploads receive stable IDs and collision-free paths `files/<file_id>/<filename>`.
Submitting their IDs stages verified bytes into Daytona after turn admission. Only
created/modified files under the verified successful artifact inventory are automatically
published. Internal roots and failed-turn outputs are not automatically archived.
Each object is uploaded and read back to verify its digest before the SQL transaction
advertises it. Required publication failure blocks successful completion and never
re-executes generated code.

SQL rollback cannot remove object bytes. Operators can invoke
`ObjectStorage(settings).collect_orphans(store)` to remove unreferenced Fleet objects
older than 24 hours; it rechecks database references and never scans MLflow buckets.
Do not rely on bucket lifecycle expiration or S3 versioning for this policy.

Opening the workspace browser reads cached inventory without creating a sandbox.
Explicit refresh uses existing leased workspace operations, may start a sandbox, and
returns busy during execution. The cache displays its last complete scan time; it is
not proof of current Volume contents. Workspace-only downloads still use Daytona.
Managed roots are `files`, `artifacts`, `memory`, `skills`, `buffers`, and `sessions`.
Path validation and symlink restrictions remain enforced. Published downloads use
short-lived private URLs and do not require a live sandbox.

## Browser observation

`GET /sessions/{id}/messages` returns a bounded chronological page of turn messages;
`before` is the exclusive turn sequence cursor. `GET /sessions/{id}/updates` sends
initial saved state and subsequent changed snapshots at two-second intervals. It is a
read-only observer: no runtime lease, event replay guarantee, or execution cancellation
on disconnect. The submitting client's existing turn SSE retains its cancellation
contract. Detailed model/tool activity is streamed live and retained through MLflow,
not duplicated as a database event timeline.

## Local MLflow on Neon

Set `FLEET_MLFLOW_LOCAL_BACKEND_STORE_URI` to a dedicated MLflow PostgreSQL database,
using the SQLAlchemy `postgresql+psycopg` driver. Set
`FLEET_MLFLOW_LOCAL_ARTIFACT_DESTINATION=s3://mlflow-artifacts` and provide
`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION`, and
`MLFLOW_S3_ENDPOINT_URL` to the managed MLflow process. MLflow owns its schema/migrations
and proxies artifacts. Existing local SQLite/experiment artifact locations are not
migrated automatically. Unconfigured local mode retains SQLite/local-file defaults.

## PostgreSQL checks

For real PostgreSQL regressions, explicitly set `FLEET_TEST_POSTGRES_URL` and run
`uv run pytest tests/store/test_store.py -k real_postgres`.
