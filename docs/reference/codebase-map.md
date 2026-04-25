# Backend Codebase Map

This document summarizes the current backend package layout with the live runtime core first and the transport shell called out explicitly.

## Top-Level Areas

| Path | Role | Notes |
| --- | --- | --- |
| `src/fleet_rlm/runtime/` | runtime core | shared chat logic, recursive execution, execution helpers, runtime models, tools, content helpers, and offline quality |
| `src/fleet_rlm/integrations/daytona/` | Daytona substrate | interpreter, runtime/session lifecycle, filesystem helpers, diagnostics, and volume access |
| `src/fleet_rlm/api/` | transport shell | FastAPI app factory, auth, routers, schemas, websocket transport, runtime services, and event shaping |
| `src/fleet_rlm/cli/` | operator surface | `fleet` / `fleet-rlm` entrypoints, command registration, and terminal UX |
| `src/fleet_rlm/ui/` | packaged UI assets | built frontend artifacts for installed distributions |
| `src/fleet_rlm/utils/` | shared helpers | small reusable utilities |

## Layer Map

```mermaid
graph TB
    CLI["cli/"] --> API
    CLI --> RUNTIME
    CLI --> INTEGRATIONS

    API["api/"] --> RUNTIME["runtime/"]
    API --> INTEGRATIONS["integrations/"]
    API --> UI["ui/"]

    RUNTIME --> DAYTONA["integrations/daytona/"]
    RUNTIME --> QUALITY["runtime/quality/"]
```

## Current Dependency Boundaries

### `src/fleet_rlm/api/`

- Incoming:
  - CLI server entrypoints
  - frontend HTTP and websocket clients
  - tests
- Outgoing:
  - `src/fleet_rlm/runtime/*`
  - `src/fleet_rlm/integrations/*`

Key files:

- `api/main.py` owns app factory, lifespan, route registration, and SPA mounting
- `api/bootstrap.py` handles startup wiring, critical state, and optional warmup
- `api/routers/ws/endpoint.py` owns the two websocket surfaces
- `api/runtime_services/chat_runtime.py` prepares execution turns and runtime context
- `api/runtime_services/chat_persistence.py` writes turn/session lifecycle data
- `api/events/events.py` shapes execution-event payloads for passive subscribers

### `src/fleet_rlm/runtime/` and `src/fleet_rlm/integrations/daytona/`

- Incoming:
  - `api/*`
  - `cli/runners.py`

- Outgoing:
  - `src/fleet_rlm/integrations/database/*`
  - external Daytona SDK and provider systems

Key files:

- `api/routers/ws/stream.py` handles websocket streaming and message loop coordination
- `runtime/factory.py` builds the canonical Daytona-backed chat agent
- `runtime/agent/agent.py` and `runtime/agent/runtime.py` contain the main cognition loop
- `runtime/execution/*` contains execution helpers and streaming event construction
- `runtime/models/*` contains runtime model assembly and registry code
- `runtime/quality/*` is the offline evaluation and optimization layer
- `integrations/daytona/interpreter.py` and `integrations/daytona/runtime.py` are the sandbox and durable-workspace substrate

### `src/fleet_rlm/cli/`

- Incoming:
  - package entrypoints
  - tests
- Outgoing:
  - `api/*`
  - `runtime/*`
  - `integrations/*`

Key files:

- `cli/main.py` is the lightweight `fleet` launcher
- `cli/fleet_cli.py` defines the `fleet-rlm` surface
- `cli/runners.py` assembles shared runtime helpers
- `cli/runtime_factory.py` remains a compatibility re-export only

## Read First by Task

| Task | Read first |
| --- | --- |
| Websocket or runtime contract change | `api/main.py`, `api/routers/ws/endpoint.py`, `api/runtime_services/chat_runtime.py`, `api/routers/ws/stream.py` |
| Session/history change | `api/routers/sessions.py`, `integrations/local_store.py`, `api/runtime_services/chat_persistence.py` |
| Runtime settings or diagnostics | `api/routers/runtime.py`, `api/runtime_services/settings.py`, `api/runtime_services/diagnostics.py` |
| Daytona execution change | `runtime/factory.py`, `runtime/agent/agent.py`, `integrations/daytona/interpreter.py`, `integrations/daytona/runtime.py` |
| Offline optimization change | `runtime/quality/module_registry.py`, `runtime/quality/optimization_runner.py` |

## Historical Note

Older docs may still refer to bridge packages that are no longer present in the tree. Treat those references as historical context only; do not use them as current ownership labels.
