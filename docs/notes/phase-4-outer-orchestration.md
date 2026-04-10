# Phase 4: minimal outer orchestration layer

## What moved outward in this phase

- `src/fleet_rlm/orchestration_app/` now owns the first real outer workflow around the existing worker boundary.
- HITL continuation state now lives in the outer layer:
  - worker-streamed `hitl_request` events are checkpointed there
  - `resolve_hitl` continuation decisions now delegate there
  - the outer layer persists a minimal workflow stage plus pending approval checkpoint in the existing session/manifest state

## What still remains temporarily in `api/orchestration`

- `api/orchestration/hitl_policy.py` remains as compatibility glue so websocket transport can keep importing the same seam while delegating outward.
- `api/orchestration/session_policy.py` still owns same-process session restore/switch policy.
- `api/orchestration/terminal_policy.py` still owns terminal ordering and completion cleanup.
- `api/orchestration/repl_bridge.py` and `api/orchestration/startup_status.py` still stay near websocket/runtime integration because those behaviors have not moved to outer orchestration yet.

## How the outer layer interacts with the worker boundary

- Websocket transport still builds a `WorkspaceTaskRequest`.
- `src/fleet_rlm/orchestration_app/coordinator.py` wraps `stream_workspace_task(...)` instead of reaching into runtime internals.
- The worker remains the specialist one-task runtime; the outer layer only adds checkpoint/continuation policy around worker-native events.

## Phase 5 direction

- Move session/workflow continuation policy out of `api/orchestration/session_policy.py` into the same outer package.
- Expand the checkpoint model from pending HITL approvals to resumable workflow/session continuation tokens.
- Continue shrinking websocket transport so it remains a thin adapter over outer orchestration entrypoints plus envelope serialization.
