# Phase 7/8: Agent Framework transition host

## What moved outward in this phase

- `src/fleet_rlm/agent_host/` now owns the first real Microsoft Agent Framework workflow host.
- The websocket execution streaming path now enters that outer host before the existing `orchestration_app` + `fleet_rlm.worker` flow runs.
- `src/fleet_rlm/agent_host/startup_status.py` now owns the delayed startup-status policy; `api/orchestration/startup_status.py` remains a compatibility shim.

## What path is now hosted by Agent Framework

- `/api/v1/ws/execution` message turns now call `agent_host.stream_hosted_workspace_task(...)`.
- The Agent Framework workflow delegates to `orchestration_app.stream_orchestrated_workspace_task(...)`.
- `orchestration_app` still applies HITL checkpoint and continuation policy around the worker stream.
- `fleet_rlm.worker.stream_workspace_task(...)` remains the execution seam into recursive DSPy + Daytona behavior.

## What still remains temporarily in `orchestration_app`

- HITL checkpoint and continuation resolution.
- Same-process session/workflow continuation context.
- Terminal/completion policy around worker terminal events.

## What still remains temporarily in `api/orchestration`

- `hitl_policy.py`, `session_policy.py`, and `terminal_policy.py` remain compatibility shims.
- `repl_bridge.py` still stays near websocket/runtime integration because it is tightly coupled to interpreter callback wiring and lifecycle persistence.
- `startup_status.py` remains only as a compatibility import seam.

## How the worker boundary stays preserved

- The Agent Framework host only wraps the existing orchestration entrypoint.
- The hosted workflow never calls runtime internals directly.
- The only execution call remains `stream_orchestrated_workspace_task(...)`, which still delegates to `fleet_rlm.worker.stream_workspace_task(...)`.

## Next safe migration step

- Move REPL bridge ownership behind `agent_host/` once interpreter execution callbacks can be surfaced as worker-native or orchestration-native events without widening websocket transport.
- After that, migrate a second orchestration concern from `orchestration_app` into Agent Framework in small slices instead of replacing the worker/runtime stack.
