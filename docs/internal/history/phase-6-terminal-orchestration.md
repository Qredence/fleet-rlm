# Phase 6: outer terminal orchestration ownership

## What moved outward in this phase

- `src/fleet_rlm/orchestration_app/terminal_flow.py` now owns terminal ordering and completion policy around worker terminal events.
- The outer layer now decides:
  - terminal `RunStatus` for final / cancelled / error events
  - when terminal completion summaries are built
  - when continuation/checkpoint state is finalized or preserved
  - whether session persistence happens before or after terminal socket delivery
- Websocket transport still delivers the terminal envelope, but it now calls the outer orchestration terminal entrypoint instead of owning terminal lifecycle policy directly.

## What still remains temporarily in `api/orchestration`

- `api/orchestration/terminal_policy.py` is now compatibility delegation only.
- `api/orchestration/hitl_policy.py` and `api/orchestration/session_policy.py` remain compatibility shims for older websocket call sites.
- `api/orchestration/repl_bridge.py` and `api/orchestration/startup_status.py` still stay near websocket/runtime integration.

## How `orchestration_app` now owns terminal lifecycle policy

- Websocket transport passes terminal worker events plus the authoritative `OrchestrationSessionContext` into `orchestration_app.terminal_flow`.
- The outer terminal flow preserves unresolved HITL approval state instead of collapsing it on terminal events, while still marking non-pending workflows as completed and keeping continuation metadata intact.
- Existing completion summary shaping is preserved by reusing the current summary builder, but the outer layer now decides when run completion happens and which final status is emitted.
- The worker boundary stays intact: websocket/API still wraps `stream_workspace_task(...)` through `stream_orchestrated_workspace_task(...)` and does not reach into runtime internals.

## Phase 7 next step

- Move REPL bridge ownership out of `api/orchestration/repl_bridge.py` into the outer orchestration package.
- Move startup-status policy out of `api/orchestration/startup_status.py` without widening websocket transport.
- Continue shrinking the compatibility shims in `api/orchestration` until the package is only thin import glue or removable.
