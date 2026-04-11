# Backend Codebase Map

This document summarizes the current backend package layout with the runtime core first and the outer layers called out explicitly.

## Current Ordering

1. **Runtime core**: `src/fleet_rlm/worker/`, `src/fleet_rlm/runtime/`, `src/fleet_rlm/integrations/` (with `src/fleet_rlm/integrations/daytona/` as the execution substrate)
2. **Outer orchestration host**: `src/fleet_rlm/agent_host/`
3. **Thin transport shell**: `src/fleet_rlm/api/`
4. **Transitional bridge layers**: `src/fleet_rlm/orchestration_app/`, `src/fleet_rlm/api/orchestration/`
5. **Offline quality layer**: `src/fleet_rlm/runtime/quality/`

## Top-Level Areas

| Path | Role | Notes |
| --- | --- | --- |
| `src/fleet_rlm/worker/` | worker boundary | task contracts and worker-side adapters feeding the shared runtime |
| `src/fleet_rlm/runtime/` | recursive runtime core | chat agent, recursive runtime, execution helpers, content helpers, tools, runtime models, and quality workflows |
| `src/fleet_rlm/integrations/` | external systems boundary | config, database, MCP, observability, and provider integrations, including the Daytona execution substrate |
| `src/fleet_rlm/integrations/daytona/` | Daytona substrate | interpreter, runtime/session lifecycle, volume helpers, diagnostics, and provider bridge code |
| `src/fleet_rlm/agent_host/` | outer orchestration host | Agent Framework workflow, hosted HITL/checkpoint policy, execution events, and startup/repl bridges |
| `src/fleet_rlm/api/` | transport shell | FastAPI auth, routers, schemas, websocket transport, and API-side services |
| `src/fleet_rlm/orchestration_app/` | transitional bridge | compatibility orchestration seams retained during host migration |
| `src/fleet_rlm/api/orchestration/` | compatibility shims | API-facing adapters that preserve older orchestration entrypoints |
| `src/fleet_rlm/cli/` | operator surface | Typer commands, runner assembly, and terminal UX |
| `src/fleet_rlm/scaffold/` | developer tooling | assets installed by `fleet-rlm init` |
| `src/fleet_rlm/ui/` | packaged assets | built frontend artifacts for wheel installs |
| `src/fleet_rlm/utils/` | small shared helpers | regex and other lightweight utility helpers |

## Primary Dependency Boundaries

### `src/fleet_rlm/worker/`, `src/fleet_rlm/runtime/`, and `src/fleet_rlm/integrations/daytona/`

- Incoming:
  - `api/*`
  - `agent_host/*`
  - `cli/runners.py`
  - `integrations/mcp/server.py`
- Outgoing:
  - `src/fleet_rlm/integrations/database/*`
  - external Daytona SDK / provider systems

Key notes:

- `runtime/agent/chat_agent.py` and `runtime/agent/recursive_runtime.py` are the main cognition loop.
- `integrations/daytona/interpreter.py` and `integrations/daytona/runtime.py` are the execution and durable-memory substrate.
- `runtime/models/builders.py` and `runtime/models/registry.py` are the runtime model/build registry surface.
- `runtime/quality/*` is the offline DSPy evaluation and optimization layer.

### `src/fleet_rlm/agent_host/`

- Incoming:
  - `api/routers/ws/*`
  - tests
- Outgoing:
  - `src/fleet_rlm/orchestration_app/*`
  - `src/fleet_rlm/worker/*`

Key notes:

- `agent_host/workflow.py` is host policy around the worker seam, not the core engine.
- `agent_host/` is intentionally narrow: it should host policy, checkpoints, HITL, and execution-event coordination without becoming a second runtime.

### `src/fleet_rlm/api/`

- Incoming:
  - CLI server entrypoints
  - tests
  - frontend HTTP/WebSocket clients
- Outgoing:
  - `src/fleet_rlm/agent_host/*`
  - `src/fleet_rlm/runtime/*`
  - `src/fleet_rlm/integrations/*`

Key notes:

- `api/main.py` owns app factory, lifespan, and SPA asset mounting.
- `api/bootstrap.py` handles runtime bootstrap, critical startup, and optional warmup scheduling.
- `api/routers/ws/*` should stay transport-thin: socket lifecycle, auth-derived identity, session extraction, event shaping, and envelope delivery.
- `api/runtime_services/*` should stay service-thin: settings, diagnostics, volume browsing, and chat/runtime preparation.

### `src/fleet_rlm/orchestration_app/` and `src/fleet_rlm/api/orchestration/`

- Incoming:
  - `agent_host/*`
  - `api/routers/ws/*`
- Outgoing:
  - `src/fleet_rlm/worker/*`
  - `src/fleet_rlm/runtime/*`

Key notes:

- These are transition seams, not the long-term center of gravity.
- Prefer shrinking these packages toward only the still-needed compatibility paths.
- Avoid adding new product logic here unless the work is explicitly about the migration seam itself.

### `src/fleet_rlm/cli/`

- Incoming:
  - package entrypoints
  - tests
- Outgoing:
  - `api/*`
  - `runtime/*`
  - `integrations/*`
  - `scaffold/*`

Key notes:

- `fleet web` delegates into `fleet-rlm serve-api`.
- Terminal UX belongs here, not in API or runtime transport code.

## Cross-Cutting Runtime Boundaries

These boundaries must stay aligned when making non-trivial backend changes:

1. Websocket contract
   `api/routers/ws/*`, `api/events/*`, `agent_host/*`, `runtime/execution/*`, `src/frontend/src/lib/rlm-api/*`, and the workspace stores/screens.
2. Daytona execution contract
   `api/schemas/core.py`, `api/routers/ws/types.py`, `worker/*`, `runtime/agent/*`, `integrations/daytona/*`, and the frontend runtime settings flow.
3. Persistence and trace shaping
   `integrations/database/*`, `api/events/*`, and `/api/v1/sessions/state`.
4. CLI/runtime assembly
   `cli/fleet_cli.py`, `cli/commands/serve_cmds.py`, `api/main.py`, and `integrations/mcp/server.py`.

## Discoverability Hotspots

The main complexity clusters are now:

1. `runtime/agent/*` and `integrations/daytona/*`
   The core reasoning and execution substrate live here.
2. `agent_host/*`
   Hosted policy is narrow, but it is real and should stay explicit in docs.
3. `api/routers/ws/*`
   The transport layer is thin by intent, but websocket changes still cascade into frontend workbench rendering.
4. `orchestration_app/*` and `api/orchestration/*`
   These are migration-heavy seams that benefit from continued cleanup and clearer ownership notes.

## Verification

The current map was checked against the source tree with:

```bash
# from repo root
find src/fleet_rlm -maxdepth 2 -type d | sort
rg --files src/fleet_rlm
```
