# Fleet CLI

## Commands

```bash
uv run fleet cli [--host 127.0.0.1] [--port 8000] [--reload] [--allow-non-loopback-bind] [-- <pi-tui args>]
uv run fleet deno [--host 127.0.0.1] [--port 8000] [--reload] [--allow-non-loopback-bind] [-- <pi-tui args>]
uv run fleet doctor daytona
uv run fleet web [--host 127.0.0.1] [--port 8000] [--reload] [--allow-non-loopback-bind]
uv run fleet-rlm serve-api [--host 127.0.0.1] [--port 8000] [--reload] [--allow-non-loopback-bind]
```

Fleet has no caller authentication. Launchers default to `127.0.0.1` and reject
non-loopback hosts (`0.0.0.0`, LAN addresses, hostnames other than `localhost`)
unless `--allow-non-loopback-bind` is supplied deliberately.
`fleet cli` forces Daytona; `fleet deno` forces Deno. Each starts the backend in
its own process group, waits up to 30 seconds for readiness, and runs pi-tui in
the foreground. Node 22.19+, pnpm, the installed TUI workspace, and an unused
port are required.

Backend output goes to timestamped files under `.fleet_rlm/logs/`; `latest.log`
points to the active launch. `Ctrl+C` reaches pi-tui, and backend shutdown
escalates from termination to forced stop after five seconds.

Before starting Daytona, `fleet cli` verifies the configured database is at the
canonical Alembic head. It never applies migrations. Recover with
`uv run python scripts/db_init.py` and retry. `fleet deno` skips this preflight.

Forward terminal options after `--`:

```bash
uv run fleet cli -- --session <session-uuid>
uv run fleet deno -- --api-url http://127.0.0.1:8000
uv run fleet cli -- artifact <artifact-uuid> --output ./result.bin
```

Artifact mode downloads content, checks length and SHA-256, fsyncs a temporary
file, and atomically renames it. It does not start the interactive screen.

`fleet web` and `fleet-rlm serve-api` are backend-only and preserve the selected
`FLEET_RUN_ENVIRONMENT`. The standalone
`pnpm --dir tools/fleet-tui start -- [options]` command connects pi-tui to an
already-running API.

## Daytona doctor

`fleet doctor daytona` validates required settings, database connectivity and
Alembic head, provider authentication, Volume visibility, scoped mounting, and
interpreter execution. It creates one uniquely labelled disposable Sandbox,
deletes it in `finally`, creates no Fleet domain rows, and prints only bounded
categories and corrective actions.
