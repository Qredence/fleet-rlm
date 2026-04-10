# Phase 5: outer session orchestration ownership

## What moved outward in this phase

- `src/fleet_rlm/orchestration_app/sessions.py` now owns authoritative same-process session restore/switch policy.
- The outer orchestration session context now carries:
  - workflow stage
  - pending approval state
  - continuation token / resumable continuation metadata
  - session-record linkage (`key`, manifest path, DB session id)
- HITL continuation state now keeps a minimal continuation snapshot even after resolution or terminal completion, so continuation ownership stays outside websocket transport.

## What still remains temporarily in `api/orchestration`

- `api/orchestration/session_policy.py` is now compatibility delegation only.
- `api/orchestration/hitl_policy.py` remains a compatibility shim for older call sites.
- `api/orchestration/terminal_policy.py` still owns terminal ordering/completion cleanup.
- `api/orchestration/repl_bridge.py` and `api/orchestration/startup_status.py` still stay near websocket/runtime integration.

## How `orchestration_app` now owns session continuation

- Websocket transport resolves session identity, then calls outer orchestration session entrypoints to restore or switch session state.
- The returned `OrchestrationSessionContext` is the authoritative continuation model passed into worker-stream orchestration and HITL resolution.
- The worker boundary is unchanged: outer orchestration still wraps `stream_workspace_task(...)` and does not reach into runtime internals.
- Session/manifest persistence continues using the existing record and manifest layout; the outer layer writes its orchestration checkpoint into that existing metadata.

## Phase 6 next step

- Move terminal ordering/completion policy out of `api/orchestration/terminal_policy.py`.
- Re-home the REPL bridge and startup-status seams under the outer orchestration package without widening the worker boundary.
- Keep websocket/API limited to transport parsing, envelope serialization, and persistence glue.
