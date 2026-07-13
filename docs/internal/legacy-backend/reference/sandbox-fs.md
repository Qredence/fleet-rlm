# Sandbox File System

This reference describes the runtime filesystem model used by `DaytonaInterpreter`.

## Mount Model

- `/home/daytona/memory`: persistent Daytona volume mount root
- `/workspace`: ephemeral working directory for runtime operations
- `/src`: injected package/runtime code inside the sandbox process

The mounted durable roots under `/home/daytona/memory` are:

- `memory/`
- `memories/`
- `knowledge/`
- `skills/`
- `sessions/`
- `logs/`
- `artifacts/`
- `buffers/`
- `uploads/`
- `meta/`

## Persistent Session State Layout

WebSocket session manifests are persisted under:

```text
sessions/<session_id>/conversation.json
```

This path is managed by `src/fleet_rlm/api/runtime_services/session_manifest.py`
and persisted through `src/fleet_rlm/api/runtime_services/session_persistence.py`.

## Common Persistence Helpers

Sandbox-side helpers exposed by the driver include:

- `save_to_volume(path, payload)`
- `load_from_volume(path)`
- workspace helpers for local ephemeral file access

These are wired through `src/fleet_rlm/runtime/execution/core_driver.py` and bundled from
the Daytona sandbox setup path in `src/fleet_rlm/integrations/daytona/sandbox_executor.py`.

## Operational Notes

- Treat `/workspace` as ephemeral and per-run.
- Treat `/home/daytona/memory` as durable shared storage scoped by mount and path discipline.
- Session manifests include logs, memory snapshots, document aliases, and metadata revisions.
- Transcript persistence is not the same as durable memory. Reusable memory
  belongs in explicit memory stores on the mounted volume, and generated
  artifacts must be promoted to durable volume paths before sandbox deletion if
  they need to survive.
- Root sandboxes can pause, resume, archive, or delete according to lifecycle
  policy. Child/delegated sandboxes should remain delete-after-task by default;
  important child outputs must be returned to the parent or saved to the
  durable volume before teardown.
- Logs and events are product-facing observability data. Correlate them by
  run/session/sandbox/tool/artifact/memory ids where available and redact
  secrets before frontend emission or durable storage.
