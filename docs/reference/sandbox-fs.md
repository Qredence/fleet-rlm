# Sandbox File System

This reference describes the runtime filesystem model used by `DaytonaInterpreter`.

## Mount Model

- `/home/daytona/memory`: persistent Daytona volume mount root
- `/workspace`: ephemeral working directory for runtime operations
- `/src`: injected package/runtime code inside the sandbox process

The mounted durable roots under `/home/daytona/memory` are `memory/`, `artifacts/`, `buffers/`, and `meta/`.

## Persistent Session State Layout

WebSocket session manifests are persisted under:

```text
meta/workspaces/<workspace_id>/users/<user_id>/react-session-<session_id>.json
```

This path is built in `src/fleet_rlm/api/routers/ws/session.py`.

## Common Persistence Helpers

Sandbox-side helpers exposed by the driver include:

- `save_to_volume(path, payload)`
- `load_from_volume(path)`
- workspace helpers for local ephemeral file access

These are wired through `src/fleet_rlm/runtime/execution/core_driver.py` and bundled from
`src/fleet_rlm/runtime/execution/sandbox_assets.py`.

## Operational Notes

- Treat `/workspace` as ephemeral and per-run.
- Treat `/home/daytona/memory` as durable shared storage scoped by mount and path discipline.
- Session manifests include logs, memory snapshots, document aliases, and metadata revisions.
