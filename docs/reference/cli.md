# Fleet CLI

## Commands

```bash
uv run fleet cli [--host 127.0.0.1] [--port 8000] [--profile NAME] [--reload] [--allow-non-loopback-bind] [-- <pi-tui args>]
uv run fleet doctor daytona
uv run fleet web [--host 127.0.0.1] [--port 8000] [--profile NAME] [--reload] [--allow-non-loopback-bind]
uv run fleet-rlm serve-api [--host 127.0.0.1] [--port 8000] [--profile NAME] [--reload] [--allow-non-loopback-bind]
```

Fleet has no caller authentication. Launchers default to `127.0.0.1` and reject
non-loopback hosts (`0.0.0.0`, LAN addresses, hostnames other than `localhost`)
unless `--allow-non-loopback-bind` is supplied deliberately.
Set `[config] default_profile` in `config/fleet.toml` to a Daytona profile before
starting a backend. The TUI `/profiles` command edits that key interactively for
the next restart. `fleet cli` accepts only profiles whose run
environment is `daytona`; any other selection fails before database preflight,
MLflow startup, or backend spawning. The
launcher starts the backend in its own process group, waits up to 90 seconds for
Daytona readiness, and runs pi-tui in the foreground. Node 22.19+, pnpm, the
installed TUI workspace, and an unused port are required.

Use `--profile NAME` when a launcher must select a policy explicitly. The
selected TOML profile is validated before provider, database, Daytona, or TUI
process initialization; environment variables can provide only values named by
that profile. Omitting `--profile` preserves the committed
`config.default_profile` behavior. An explicit profile cannot be combined with
`--reload`, because an in-memory validated settings object cannot be carried
safely into a Uvicorn reloader process.

For the shipped `daytona-recursive` policy, `fleet cli`
starts the installed MLflow server on
`127.0.0.1:5001` with one worker, SQLite metadata under
`.fleet_rlm/mlflow/mlflow.db`, and artifacts under
`.fleet_rlm/mlflow/artifacts`. It reuses an already-running server only when
`GET /version` matches the installed MLflow version, and it never stops a
reused process. Custom policies may disable tracing or select another tracking
destination. Standalone backend commands require the configured tracking server
to be started separately.

### Upgrade the local MLflow store to 3.16

Fleet pins MLflow `3.16.1` and does not apply MLflow schema migrations during
startup. MLflow owns this database schema; Fleet's Alembic migrations manage a
separate Fleet database. Follow the [official MLflow migration guidance](https://mlflow.org/docs/latest/self-hosting/migration/)
and stop Fleet plus every other writer to the local MLflow database before
starting this runbook.

1. Record the previous `uv.lock`, the database URI, and the artifact location.
   Keep the previous dependency environment available for rollback.
2. Create and verify a consistent SQLite backup using SQLite's backup facility:

   ```bash
   DB="$PWD/.fleet_rlm/mlflow/mlflow.db"
   BACKUP="$PWD/.fleet_rlm/mlflow/mlflow.db.pre-3.16"
   DISPOSABLE="$PWD/.fleet_rlm/mlflow/mlflow.db.rehearsal-3.16"
   sqlite3 "$DB" ".backup '$BACKUP'"
   sqlite3 "$BACKUP" "PRAGMA integrity_check;"
   cp "$BACKUP" "$DISPOSABLE"
   ```

   Continue only when the integrity check returns `ok`; retain the original
   database and backup separately from the rehearsal copy.
3. Rehearse the upgrade against the disposable copy with the MLflow 3.16
   environment:

   ```bash
   uv run mlflow db upgrade "sqlite:///$DISPOSABLE"
   sqlite3 "$DISPOSABLE" "PRAGMA integrity_check;"
   ```

4. After the rehearsal succeeds, run `mlflow db upgrade` against the real
   database, restart the supervised server, and verify the installed version:

   ```bash
   uv run mlflow db upgrade "sqlite:///$DB"
   curl --fail http://127.0.0.1:5001/version
   ```

   Verify trace retrieval from a fresh MLflow client process using a known
   trace ID (see [Inspect a trace from a script](#inspect-a-trace-from-a-script)),
   and confirm the existing artifact location is unchanged. Check both the
   execution and preparation traces when phase links are enabled.
5. If rollback is required, stop Fleet and all MLflow writers again. Preserve
   the upgraded database as an investigation copy, restore the verified
   pre-upgrade database, and restart with the previous lock and dependency
   environment. Do not discard the upgraded copy until the investigation is
   complete.

### Inspect a trace from a script

Use this recipe to print the span tree of one Turn. Get the trace ID from
`/trace` in the TUI or from the MLflow UI.

The supervised server keeps span data behind `mlflow-artifacts:` URIs. MLflow
resolves these URIs with the global tracking URI, not with the `tracking_uri`
argument of `MlflowClient`. Thus `MlflowClient(tracking_uri=...)` alone can
fail with "the tracking URI must be a valid http or https URI". Set the global
tracking URI before you fetch the trace:

```bash
export MLFLOW_TRACKING_URI=http://127.0.0.1:5001
uv run python - "$TRACE_ID" <<'EOF'
import sys

import mlflow

trace = mlflow.get_trace(sys.argv[1])
children = {}
for span in trace.data.spans:
    children.setdefault(span.parent_id, []).append(span)


def show(parent_id, depth=0):
    for span in sorted(children.get(parent_id, []), key=lambda s: s.start_time_ns):
        print(f"{'  ' * depth}{span.name} [{span.status.status_code.value}]")
        show(span.span_id, depth + 1)


show(None)
EOF
```

When the server is stopped, read the store directly. Open the database in
read-only mode so that the script cannot change the store. Run this from the
repository root:

```bash
uv run python - "$TRACE_ID" <<'EOF'
import sqlite3
import sys

db = sqlite3.connect("file:.fleet_rlm/mlflow/mlflow.db?mode=ro", uri=True)
rows = db.execute(
    "SELECT span_id, parent_span_id, name, status FROM spans "
    "WHERE trace_id = ? ORDER BY start_time_unix_nano",
    (sys.argv[1],),
).fetchall()
children = {}
for span_id, parent_id, name, status in rows:
    children.setdefault(parent_id, []).append((span_id, name, status))


def show(parent_id, depth=0):
    for span_id, name, status in children.get(parent_id, []):
        print(f"{'  ' * depth}{name} [{status}]")
        show(span_id, depth + 1)


show(None)
EOF
```

The `spans` table holds only traces that MLflow tagged with
`mlflow.trace.spansLocation=TRACKING_STORE`. For other traces, the span data is
in `.fleet_rlm/mlflow/artifacts/<experiment_id>/traces/<trace_id>/artifacts/traces.json`.
The `content` column of each `spans` row holds the full span JSON, which
includes attributes and events.

### Recommended Trace V4 view

Configure the view in the MLflow UI with the existing bounded trace fields:
`fleet.session_id`, `fleet.trace_phase`, `fleet.run_id`, `fleet.turn_status`,
`fleet.latency_ms`, `fleet.models`, `fleet.providers`, `fleet.tools`,
`fleet.total_tokens`, `fleet.cache_read_tokens`, and
`fleet.cache_creation_tokens`. Keep Trace ID, status, start/end time, and error
state visible as the operational columns. The preparation-to-execution Span
Link is enabled only for the supervised loopback MLflow server; managed Unity
Catalog traces continue to use the `fleet.preparation_trace_id` tag for
correlation.

Backend and owned MLflow output go to timestamped files under
`.fleet_rlm/logs/`; `latest.log` and `mlflow-latest.log` point to the active
logs. `Ctrl+C` reaches pi-tui, and owned-process shutdown escalates from
termination to forced stop after five seconds.

Before starting Daytona, `fleet cli` verifies the configured database is at the
canonical Alembic head. It never applies migrations. Recover with
`uv run python scripts/db_init.py` and retry.

Forward terminal options after `--`:

```bash
uv run fleet cli -- --session <session-uuid>
uv run fleet cli -- artifact <artifact-uuid> --output ./result.bin
```

Artifact mode downloads content, checks length and SHA-256, fsyncs a temporary
file, and atomically renames it. It does not start the interactive screen.

`fleet web` and `fleet-rlm serve-api` are backend-only and use the profile
selected by `[config] default_profile`, unless `--profile NAME` is supplied.
The standalone
`pnpm --dir tools/fleet-tui start -- [options]` command connects pi-tui to an
already-running API.

### Phase 6 evaluation planning

Use the maintained Phase 6 runner for current evaluation workflows. These
commands create provider-free planning artifacts; they do not execute models,
Daytona, or MLflow and do not establish campaign results:

```bash
uv run python scripts/benchmarks/run_rlm_latency.py phase6-plan \
  --output .scratch/evals/phase6-plan.json
uv run python scripts/benchmarks/run_rlm_latency.py phase6-dry-run \
  --output .scratch/evals/phase6-dry-run.json
```

See the [evaluation and optimization guide](../how-to-guides/evaluation-optimization.md)
for current operator workflows and their authorization requirements.

## Daytona doctor

`fleet doctor daytona` validates required settings, database connectivity and
Alembic head, provider authentication, Volume visibility, scoped mounting, and
interpreter execution. It creates one uniquely labelled disposable Sandbox,
deletes it in `finally`, creates no Fleet domain rows, and prints only bounded
categories and corrective actions.
