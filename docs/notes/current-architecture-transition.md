# Current Architecture and Transition Note

This note is historical context for readers who still encounter older migration-era language in the docs.

## Historical Context

The backend used to be described with bridge-layer terminology during the migration to the current layout. Those labels are historical only and are no longer present in the current tree.

- `orchestration_app/` is historical terminology for an old compatibility bridge.
- `api/orchestration/` is historical terminology for old compatibility shims.
- The current ownership story is `api/` -> `agent_host/` -> `worker/` -> `runtime/` -> `integrations/daytona/`.
- Offline evaluation and optimization remain in `runtime/quality/`.

## Current Read Order

When you want the live architecture, read these files first:

1. `src/fleet_rlm/api/main.py`
2. `src/fleet_rlm/api/routers/ws/endpoint.py`
3. `src/fleet_rlm/api/runtime_services/chat_runtime.py`
4. `src/fleet_rlm/agent_host/workflow.py`
5. `src/fleet_rlm/agent_host/terminal_flow.py`
6. `src/fleet_rlm/worker/streaming.py`
7. `src/fleet_rlm/runtime/factory.py`
8. `src/fleet_rlm/runtime/agent/chat_agent.py`
9. `src/fleet_rlm/integrations/daytona/interpreter.py`
10. `src/fleet_rlm/integrations/daytona/runtime.py`

## What the Current Tree Actually Means

- `api/` is transport-thin: auth, websocket lifecycle, runtime services, session/history, and event shaping.
- `agent_host/` is a real hosted policy layer: workflow orchestration, HITL, terminal ordering, checkpoints, and startup-status handling.
- `worker/`, `runtime/`, and `integrations/daytona/` hold the execution core and sandbox substrate.
- `runtime/quality/` is offline only and should not be treated as part of the live websocket request path.

## Historical Cleanup Rule

If older notes or reviews still mention bridge packages, treat them as migration history. Do not use them as current ownership labels when updating docs or code.
