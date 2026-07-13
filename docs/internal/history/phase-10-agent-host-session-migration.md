# Phase 10: Agent host session migration

## What moved into `agent_host`

- `src/fleet_rlm/agent_host/sessions.py` now owns the hosted-path orchestration session context, session-record linkage, workflow-stage state, continuation-token state, and session-switch restoration flow.
- Hosted websocket execution and `resolve_hitl` now build and consume session/workflow continuation context through `fleet_rlm.agent_host`, so the same outer host owns both HITL policy and the continuation state that policy mutates.

## What still remains in `orchestration_app`

- `src/fleet_rlm/orchestration_app/coordinator.py` still preserves the compatibility `stream_orchestrated_workspace_task(...)` path and the stable `fleet_rlm.worker.stream_workspace_task(...)` seam.
- `src/fleet_rlm/orchestration_app/terminal_flow.py` still owns terminal/completion policy for now.
- `src/fleet_rlm/orchestration_app/sessions.py` is now a compatibility shim that re-exports the host-owned session helpers until remaining compatibility imports are removed.

## What still remains in `api/orchestration`

- `api/orchestration/hitl_policy.py`, `session_policy.py`, `startup_status.py`, and `terminal_policy.py` remain compatibility shims.
- `api/orchestration/repl_bridge.py` still stays transport/runtime-adjacent because interpreter callback bridging and lifecycle persistence have not moved yet.

## How the worker boundary stays preserved

- The hosted execution path remains websocket/API transport -> `agent_host.stream_hosted_workspace_task(...)` -> compatibility orchestration path -> `fleet_rlm.worker.stream_workspace_task(...)`.
- `agent_host` now owns outer continuation/session policy, but it still never calls runtime internals directly.
- Recursive DSPy + `dspy.RLM` behavior and Daytona execution therefore remain behind the worker seam.

## Next safe migration step

- Move the remaining REPL bridge and adjacent terminal-ownership seams behind `agent_host` in another narrow slice, keeping websocket transport thin while continuing to shrink `orchestration_app`.
