# Backend Codebase Map

This document summarizes the current backend package layout after the
simplification pass.

## Top-Level Areas

| Path | Role | Notes |
| --- | --- | --- |
| `src/fleet_rlm/api/` | transport | FastAPI auth, routers, schemas, websocket transport, and API-side helpers |
| `src/fleet_rlm/cli/` | operator surface | Typer commands, runner assembly, and terminal UX |
| `src/fleet_rlm/runtime/` | shared runtime | chat agent, execution engine, content helpers, tools, and runtime models |
| `src/fleet_rlm/integrations/` | external systems | config, database, MCP, observability, and provider backends |
| `src/fleet_rlm/scaffold/` | developer tooling | assets installed by `fleet-rlm init` |
| `src/fleet_rlm/ui/` | packaged assets | built frontend artifacts for wheel installs |
| `src/fleet_rlm/utils/` | small shared helpers | regex and Modal utility helpers |

## Primary Dependency Boundaries

### `src/fleet_rlm/api/`

- Incoming:
  - CLI server entrypoints
  - tests
  - frontend HTTP/WebSocket clients
- Outgoing:
  - `src/fleet_rlm/runtime/*`
  - `src/fleet_rlm/integrations/*`

Key notes:

- `api/main.py` owns app factory, lifespan, and SPA asset mounting.
- `api/bootstrap.py` now splits critical startup from optional background warmup. Persistence/auth are boot blockers; planner/delegate LM warmup plus MLflow/PostHog startup are not.
- `api/routers/ws/` is intentionally consolidated into:
  - `endpoint.py`, `stream.py`, `session.py`, `commands.py`, and `types.py` as the main transport surface
  - with `helpers.py`, `lifecycle.py`, `messages.py`, `persistence.py`, `manifest.py`, `artifacts.py`, `runtime.py`, `turn_setup.py`, and `turn_lifecycle.py` handling transport-safe socket operations and durable run state
  - plus focused support modules for failures, HITL, terminal ordering, completion summaries, and execution-event plumbing
- `api/runtime_services/` is split into:
  - `settings.py` for runtime settings writes
  - `diagnostics.py` for status/connectivity checks
  - `volumes.py` for volume browsing

### `src/fleet_rlm/runtime/`

- Incoming:
  - `api/*`
  - `cli/runners.py`
  - `integrations/mcp/server.py`
- Outgoing:
  - `src/fleet_rlm/integrations/daytona/*`
  - `src/fleet_rlm/integrations/database/*`

Key notes:

- `runtime/agent/signatures.py` is the canonical DSPy signature surface.
- `runtime/agent/*` remains the shared ReAct + `dspy.RLM` orchestration surface.
- `runtime/execution/*` owns interpreter/session/streaming behavior.
- `runtime/content/*` replaces the old split across chunking/document-ingestion/log “features”.
- `runtime/tools/*` is the typed tool-adapter surface consumed by the shared chat runtime.
- `runtime/models/rlm_runtime_modules.py` remains the DSPy runtime-module assembly point shared by the agent runtime.

### `src/fleet_rlm/integrations/`

- Incoming:
  - `api/*`
  - `runtime/*`
  - `cli/*`
- Outgoing:
  - external systems only

Key notes:

- `integrations/config/*` replaces the old top-level `conf/` split.
- `integrations/observability/*` now owns telemetry and tracing only.
- `runtime/quality/*` owns DSPy evaluation, optimization, and scorer wiring.
- `integrations/daytona/*` is the canonical Daytona implementation surface, centered on `interpreter.py`, `runtime.py`, and `bridge.py`.
- `integrations/mcp/server.py` remains the MCP entrypoint.

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

- `fleet web` still delegates into `fleet-rlm serve-api`.
- `cli/terminal/*` replaces the old terminal “feature” namespace.

## Cross-Cutting Runtime Boundaries

These boundaries must stay aligned when making non-trivial backend changes:

1. Websocket contract
   `api/routers/ws/*`, `api/events/*`, `runtime/execution/*`,
   `src/frontend/src/lib/rlm-api/*`, and the workspace stores/screens.
2. Daytona execution contract
   `api/schemas/core.py`, `api/routers/ws/types.py`,
   `integrations/daytona/*`, and the frontend runtime settings flow.
3. Persistence and trace shaping
   `integrations/database/*`, `api/events/*`, and `/api/v1/sessions/state`.
4. CLI/runtime assembly
   `cli/fleet_cli.py`, `cli/commands/serve_cmds.py`, `api/main.py`,
   and `integrations/mcp/server.py`.

## Simplification Hotspots

The largest remaining complexity centers are:

1. `runtime/agent/*` and `runtime/execution/*`
   Shared runtime behavior is still spread across multiple focused modules.
2. `integrations/daytona/*`
   Daytona is now flattened at the provider root, but it is still a large subsystem.
3. `api/routers/ws/*`
   The transport split is greatly reduced, but websocket changes still cascade into frontend workbench rendering.
4. `scaffold/`
   This package is intentionally asset-heavy and should stay data-oriented rather than gaining logic wrappers.

## Verification

The current map was checked against the source tree with:

```bash
# from repo root
find src/fleet_rlm -maxdepth 2 -type d | sort
rg --files src/fleet_rlm
```
