# Database and persistence

PostgreSQL stores durable application state. Daytona Volumes hold mutable
workspaces. Private S3-compatible storage holds published files. MLflow owns its
own database/artifacts; these stores do not share a transaction.

## Connection

Set `FLEET_DATABASE_URL` and `FLEET_DATABASE_SCHEMA`. An optional
`FLEET_DATABASE_MIGRATION_URL` supplies a separate DDL role for the same host,
port and database. Use a direct TLS PostgreSQL endpoint with Neon; Fleet retains
Psycopg pooling and sets `search_path` at startup. See
[configuration](configuration.md) for defaults.

## Ownership

| Tables | Contents |
| --- | --- |
| `sessions`, `turns`, `messages` | Settings, memory summary, execution intent/outcomes and chat history |
| `notes` | Session memories, source digests and review status |
| `files`, `message_files`, `workspace_files` | Published objects, message links and cached workspace inventory |
| `sandbox_resources`, `leases` | Epoch-fenced allocation/cleanup intents and TTL ownership |
| `replay_snapshots`, `replay_blobs`, `review_labels` | Immutable inputs and reviewed optimization gold |
| `jobs`, `global_settings` | Evaluation/optimization lifecycle, approval and shared settings |
| `schema_migrations` | Applied schema versions |

A Turn is identified by `(session_id, turn_id)`. Successful result, assistant
message, memory update, selected replay and published-file references commit
together under the Session ownership epoch. Failed/cancelled attempts remain
visible but stay outside completed-turn context. Regeneration creates a new Turn.
Stored history never restores live interpreter state.

Startup prunes unlabelled replays older than 30 days and unreferenced blobs.
Labelled replays are retained. Selected intact snapshots are required for GEPA;
MLflow traces alone cannot replace them.

## Files and workspace

Configure `FLEET_STORAGE_ENDPOINT`, `FLEET_STORAGE_REGION`,
`FLEET_STORAGE_BUCKET`, `FLEET_STORAGE_ACCESS_KEY` and
`FLEET_STORAGE_SECRET_KEY`. Host-only credentials never enter Daytona or browsers.
Rows store object keys; downloads use short-lived private URLs.

Uploads have stable IDs and paths `files/<file_id>/<filename>`. A Turn stages
verified bytes after admission. Only verified created/modified successful outputs
are automatically published. Each upload is read back for digest verification
before SQL advertises it. Required publication failure blocks completion without
rerunning generated code.

SQL rollback cannot delete object bytes. `ObjectStorage(settings).collect_orphans(store)`
removes unreferenced Fleet objects older than 24 hours after rechecking references;
it never scans MLflow buckets.

Workspace browsing reads cached inventory. Explicit refresh may start Daytona;
a busy Session returns 409. Managed roots are `files`, `artifacts`, `memory`,
`skills`, `buffers` and `sessions`. Cache timestamps do not prove current Volume
contents. Workspace downloads use Daytona; published downloads do not.

## Local MLflow storage

For PostgreSQL, set `FLEET_MLFLOW_LOCAL_BACKEND_STORE_URI` with the SQLAlchemy
`postgresql+psycopg` driver. For S3 artifacts, set
`FLEET_MLFLOW_LOCAL_ARTIFACT_DESTINATION` and supply `AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` and `MLFLOW_S3_ENDPOINT_URL` to the
managed MLflow process. Otherwise local mode uses SQLite/local files.
