# Phase 9: Agent host HITL migration

## What moved into `agent_host`

- `src/fleet_rlm/agent_host/hitl_flow.py` now owns HITL request checkpointing, continuation-token tracking, and `resolve_hitl` continuation policy.
- `src/fleet_rlm/agent_host/checkpoints.py` now owns the checkpoint state model used to persist HITL workflow state on the hosted path.
- `src/fleet_rlm/agent_host/workflow.py` now applies HITL checkpoint policy while the Agent Framework host streams worker-compatible events.

## What still remains in `orchestration_app`

- `src/fleet_rlm/orchestration_app/coordinator.py` still preserves the compatibility `stream_orchestrated_workspace_task(...)` path and the stable `fleet_rlm.worker.stream_workspace_task(...)` execution seam.
- Session/workflow continuation context remains in `src/fleet_rlm/orchestration_app/sessions.py`.
- Terminal/completion policy remains in `src/fleet_rlm/orchestration_app/terminal_flow.py`.
- `orchestration_app/checkpoints.py` and `orchestration_app/hitl_flow.py` are now compatibility exports only and can be deleted after one more migration pass removes their remaining imports.

## What still remains in `api/orchestration`

- `api/orchestration/hitl_policy.py`, `session_policy.py`, and `terminal_policy.py` remain compatibility shims.
- `api/orchestration/repl_bridge.py` still stays near websocket/runtime integration because interpreter callback wiring and lifecycle persistence are still transport-adjacent.
- `api/orchestration/startup_status.py` remains only as a compatibility shim for the already-migrated startup-status concern.

## How the worker boundary stays preserved

- The hosted execution path is still websocket/API transport -> `agent_host.stream_hosted_workspace_task(...)` -> `orchestration_app.stream_orchestrated_workspace_task(...)` -> `fleet_rlm.worker.stream_workspace_task(...)`.
- `agent_host` now owns HITL orchestration policy around that stream, but it still never calls runtime internals directly.
- Recursive DSPy + `dspy.RLM` execution and Daytona behavior therefore stay behind the worker seam.

## Next safe migration step

- Move the REPL bridge behind `agent_host/` in a similarly narrow slice so the Agent Framework host can own another orchestration concern without widening the websocket contract.
