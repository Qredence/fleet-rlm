# Sandbox File System

This reference describes the runtime filesystem model used by `ModalInterpreter`.

## Mount Model

- `/data`: optional persistent Modal volume mount (default mount path)
- `/workspace`: ephemeral working directory for runtime operations
- `/src`: injected package/runtime code inside the sandbox process

`/data` is only present when `volume_name` is configured.

## Persistent Session State Layout

WebSocket session manifests are persisted under:

```text
/data/workspaces/<workspace_id>/users/<user_id>/memory/react-session-<session_id>.json
```

This path is built in `src/fleet_rlm/server/routers/ws/session.py`.

## Common Persistence Helpers

Sandbox-side helpers exposed by the driver include:

- `save_to_volume(path, payload)`
- `load_from_volume(path)`
- workspace helpers for local ephemeral file access

These are wired through `src/fleet_rlm/core/driver.py` and bundled from
`src/fleet_rlm/core/volume_tools.py`.

## Operational Notes

- Treat `/workspace` as ephemeral and per-run.
- Treat `/data` as durable shared storage scoped by mount and path discipline.
- Session manifests include logs, memory snapshots, document aliases, and metadata revisions.
