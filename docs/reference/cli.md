# Fleet CLI

## Commands

```bash
uv run fleet cli [--host 127.0.0.1] [--port 8000] [--reload] [--allow-non-loopback-bind] [-- <pi-tui args>]
uv run fleet doctor daytona
uv run fleet web [--host 127.0.0.1] [--port 8000] [--reload] [--allow-non-loopback-bind]
uv run fleet-rlm serve-api [--host 127.0.0.1] [--port 8000] [--reload] [--allow-non-loopback-bind]
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

For the interactive `daytona` and `daytona-recursive` profiles, `fleet cli`
starts the installed MLflow server on
`127.0.0.1:5001` with one worker, SQLite metadata under
`.fleet_rlm/mlflow/mlflow.db`, and artifacts under
`.fleet_rlm/mlflow/artifacts`. It reuses an already-running server only when
`GET /version` matches the installed MLflow version, and it never stops a
reused process. Benchmark profiles disable tracing, while standalone backend
commands require the configured tracking server to be started separately.

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
selected by `[config] default_profile`. The standalone
`pnpm --dir tools/fleet-tui start -- [options]` command connects pi-tui to an
already-running API.

## Daytona doctor

`fleet doctor daytona` validates required settings, database connectivity and
Alembic head, provider authentication, Volume visibility, scoped mounting, and
interpreter execution. It creates one uniquely labelled disposable Sandbox,
deletes it in `finally`, creates no Fleet domain rows, and prints only bounded
categories and corrective actions.
