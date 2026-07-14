# Fleet CLI

The canonical command surface contains supervised terminal, backend-only, and
diagnostic commands:

```bash
uv run fleet cli [--host 127.0.0.1] [--port 8000] [--reload] [-- <Ink args>]
uv run fleet deno [--host 127.0.0.1] [--port 8000] [--reload] [-- <Ink args>]
uv run fleet doctor daytona
uv run fleet web [--host 127.0.0.1] [--port 8000] [--reload]
uv run fleet-rlm serve-api [--host 127.0.0.1] [--port 8000] [--reload]
```

`fleet cli` forces Daytona and `fleet deno` forces Deno, starts the backend in
its own process group, waits up to 30 seconds for readiness, then runs the Ink
client in the foreground. Node 22+, pnpm, the repository TUI workspace, and an
unused port are required. Backend output is written to timestamped files under
`.fleet_rlm/logs/`; `latest.log` points to the current launch. Ink receives
`Ctrl+C`, and backend shutdown escalates from termination to a forced stop after
five seconds. Examples of terminal argument forwarding are:

```bash
uv run fleet cli -- --session <session-uuid>
uv run fleet deno -- --user-id <uuid> --workspace-id <uuid>
```

`fleet web` and `fleet-rlm serve-api` remain backend-only and preserve
`FLEET_RUN_ENVIRONMENT`. The standalone `pnpm --dir tools/fleet-tui start`
command remains useful when connecting Ink to an already-running API.

`fleet doctor daytona` is an opt-in external check. It validates required
settings, database connectivity and Alembic head, provider authentication and
Volume visibility, then creates a uniquely labelled ephemeral Sandbox using
the production workspace-subpath mount contract. It executes a fixed Python
probe and deletes the Sandbox in `finally`. The doctor creates no Fleet Session,
Run, binding, Attachment, or Artifact rows and prints only bounded categories
and corrective actions.
