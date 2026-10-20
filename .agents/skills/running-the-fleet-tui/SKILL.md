---
name: running-the-fleet-tui
description: Drive the current Fleet Lite terminal client (Textual or Rich fallback) and verify its persisted MLflow traces.
---

# Running the Fleet Lite terminal client

Read [the maintained CLI reference](../../../docs/reference/cli.md) first.
Use `uv run fleet cli` in a real PTY. Live model, Daytona, and database runs
require operator authorization. Check credential presence without printing values.
Fleet Lite initializes additive PostgreSQL tables at API startup; do not run
retired migration commands. Non-secret schema-3 defaults may be configured in
`config/fleet.toml`; credentials stay in `.env`/environment. Environment overrides
win over TOML, and file changes require a process restart.

## Launch

Choose an unused loopback API port and a unique session to ensure modified code
is running. A healthy API on the chosen port is reused rather than replaced.

```bash
FLEET_PORT=8001 uv run fleet cli --session verification-UNIQUE --mlflow-experiment fleet-verification
```

The launcher starts API/MLflow services as needed. Existing services are left
running. Logs are `.fleet/logs/server.log` and `.fleet/logs/mlflow.log`; local
MLflow data is `.fleet/mlflow`. Wait for the Textual composer
(`What would you like to do?`) or, in the non-TTY Rich fallback, the `fleet>`
prompt before input. Textual uses the alternate screen, so wait for the
composer or `Turn summary` rather than the shell prompt before snapshotting.
Do not use retired Node-TUI headers, footers, or `cli --port` arguments.

## Exercise and verify

Use `/config`, `/mode rlm`, `/mode flex`, and ordinary task text. In the
Textual client, press `Ctrl+O` or click a `▸` step to expand Daytona Python
code/output; the Rich fallback prints those panels inline. Read the Python
code/output and the final answer. A successful turn shows its trace ID and
`Turn persisted to Session history.` in the Textual Turn card; the Rich
fallback prints the same line. Use `/history`, `/turn <turn-id>`, and `/trace`
to verify durable state. During a running turn, Ctrl+C closes the stream and
requests remote cancellation; while editing, Ctrl+C clears input. Do not use
Escape as a cancellation command. After cancellation, check durable state
before retrying: disconnect is not rollback.

Fetch complete traces using MLflow CLI/API while the tracking server is available.
Use their recorded session/trace IDs, not an assumed experiment ID or old database
path. Inspect execution code/output, span status, daemon protocol, and effective
Turn timeout. A corrected Python error should be a failed execution span inside
a successful overall turn. Do not add overlapping outer and inner span durations.

## Finish

Exit using `/exit`. The launcher stops only services it started, API first so
traces flush before MLflow exits. Retain the transcript, trace IDs, and acceptance
results in a task-owned scratch directory. Confirm cleanup of verification
sandboxes without deleting unrelated resources.
