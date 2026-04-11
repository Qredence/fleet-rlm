# Phase 11: Agent host REPL bridge migration

## What moved into `agent_host`

- `src/fleet_rlm/agent_host/repl_bridge.py` now owns the hosted REPL/interpreter callback bridge for the websocket execution path.
- `src/fleet_rlm/agent_host/execution_events.py` now owns hosted execution-event normalization plus lightweight Daytona state references (`sandbox_id`, volume/workspace handles, and interpreter context references) without copying interpreter memory into workflow state.
- `src/fleet_rlm/agent_host/app.py` now starts and stops the hosted REPL bridge around the outer Agent Framework execution path, so hosted execution-event wiring lives with the host instead of websocket transport glue.

## What still remains in `orchestration_app`

- `src/fleet_rlm/orchestration_app/coordinator.py` still preserves the compatibility `stream_orchestrated_workspace_task(...)` path and the stable `fleet_rlm.worker.stream_workspace_task(...)` seam.
- `orchestration_app` still remains the compatibility layer between the outer host and the worker while terminal/completion policy continues to live outside `agent_host`.

## What still remains in `api/orchestration`

- `src/fleet_rlm/api/orchestration/repl_bridge.py` is now compatibility glue that re-exports the host-owned bridge for remaining imports.
- Other orchestration compatibility shims in `api/orchestration/` still remain until later phases retire them.

## How Daytona remains authoritative for stateful execution

- Interpreter execution still runs inside Daytona sandbox/interpreter contexts; the host only observes callback payloads and lightweight references to Daytona-owned state.
- The bridge never serializes Python globals, interpreter memory, or working execution state into Agent Framework workflow state.
- The hosted execution path remains websocket/API transport -> `agent_host` -> compatibility orchestration path -> `fleet_rlm.worker` -> Daytona execution substrate.

## Next safe migration step

- Move the remaining terminal/completion orchestration seams behind `agent_host`, then continue the planned worker-side DSPy/GEPA quality-layer work without widening websocket transport ownership.
