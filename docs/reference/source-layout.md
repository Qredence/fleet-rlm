# Source Layout (`src/fleet_rlm`)

This document reflects the current backend package structure in `src/fleet_rlm/`. Paths below are relative to that directory.

## Top-Level Areas

| Path | Description |
| --- | --- |
| `__init__.py` | Minimal public package exports and version marker. |
| `AGENTS.md` | Backend-specific contributor guidance. |
| `agent_host/` | Hosted policy layer around the worker seam, HITL, checkpoints, and terminal/session flow. |
| `api/` | FastAPI transport, auth, schemas, routers, websocket lifecycle, runtime services, and event shaping. |
| `cli/` | `fleet` / `fleet-rlm` entrypoints, command registration, runtime helpers, and terminal UX. |
| `integrations/` | Config, database, observability, MCP, Daytona, and local-store integrations. |
| `runtime/` | Shared agent loop, execution helpers, content processing, tools, runtime models, and offline quality. |
| `scaffold/` | Bundled init assets: skills, agents, hooks, and team inboxes. |
| `ui/` | Packaged frontend build assets used by installed distributions. |
| `utils/` | Small shared helpers. |

## `api/`

| Path | Description |
| --- | --- |
| `main.py` | FastAPI app factory, lifespan management, route registration, and SPA mounting. |
| `bootstrap.py` | Runtime bootstrap, critical persistence init, optional background warmup, and shutdown orchestration. |
| `config.py` | `ServerRuntimeConfig` for the HTTP/WebSocket server. |
| `dependencies.py` | Shared `ServerState` container and dependency helpers. |
| `middleware.py` | Cross-cutting HTTP middleware registration. |
| `server_utils.py` | Shared API utility helpers. |
| `auth/` | Auth providers, tenant admission, and auth types. |
| `events/` | Execution-event models, sanitization, and payload shaping for the passive event stream. |
| `routers/` | HTTP and websocket route handlers. |
| `runtime_services/` | Runtime settings, diagnostics/status, volume browsing, chat runtime prep, and persistence helpers. |
| `schemas/` | Request/response models shared across routes. |

### HTTP routers

| Path | Description |
| --- | --- |
| `routers/auth.py` | Auth identity endpoint. |
| `routers/health.py` | Health and readiness endpoints. |
| `routers/runtime.py` | Runtime settings, diagnostics, and Daytona volume browsing routes. |
| `routers/optimization.py` | GEPA/optimization routes. |
| `routers/sessions.py` | Session state, history, transcript, archive, and export routes. |
| `routers/traces.py` | Feedback and trace-reporting routes. |
| `routers/ws/` | Websocket transport for execution and execution-event subscriptions. |

### WebSocket runtime (`api/routers/ws/`)

| Path | Description |
| --- | --- |
| `endpoint.py` | `/api/v1/ws/execution` and `/api/v1/ws/execution/events` entrypoints. |
| `stream.py` | Execution turn streaming and message loop coordination. |
| `session.py` | Runtime preparation and session restore/switch helpers. |
| `turn_setup.py` | Converts a websocket payload into a prepared runtime turn. |
| `turn_runner.py` | Turn execution coordination around the worker boundary. |
| `turn_persistence.py` | Local and durable persistence hooks for a turn. |
| `commands.py` | Command-frame dispatch and run lifecycle initialization. |
| `messages.py` | Websocket message parsing and validation. |
| `terminal.py` | Terminal event shaping and ordering. |
| `completion.py` | Completion summary and workbench hydration payload assembly. |
| `hitl.py` | Human-in-the-loop request handling. |
| `manifest.py` | Session manifest handling. |
| `artifacts.py` | Artifact event helpers. |
| `execution_support.py` | Passive execution event emitter wiring. |
| `errors.py`, `failures.py`, `loop_exit.py`, `task_control.py`, `worker_request.py`, `helpers.py`, `types.py` | Focused helpers for errors, shutdown, task control, request normalization, and websocket utility code. |

## `agent_host/`

| Path | Description |
| --- | --- |
| `workflow.py` | Hosted Agent Framework workflow around the worker seam. |
| `hitl_flow.py` | HITL checkpoint and resume logic. |
| `terminal_flow.py` | Terminal ordering and completion policy. |
| `checkpoints.py` | Checkpoint helpers for hosted execution. |
| `sessions.py` | Session continuation, restore, and orchestration-context helpers. |
| `execution_events.py` | Host-side execution-event normalization and metadata shaping. |
| `repl_bridge.py` | Interpreter callback bridge into host/state handling. |
| `startup_status.py` | Delayed startup-status policy for slow initial turns. |
| `app.py`, `adapters.py`, `types.py` | Workflow adapters and host-facing types. |

## `runtime/`

| Path | Description |
| --- | --- |
| `config.py` | Planner/delegate LM bootstrap from environment. |
| `agent/` | Shared DSPy orchestration, chat/session state, delegation policy, memory, and command helpers. |
| `execution/` | Interpreter support, streaming helpers, runtime factory glue, and execution profiles. |
| `content/` | Chunking, ingestion, and execution-log processing helpers. |
| `models/` | Shared runtime/streaming models plus runtime-module assembly. |
| `quality/` | DSPy evaluation and optimization. |
| `tools/` | Typed tool adapters exposed to the shared runtime. |

### Runtime warmup policy

- FastAPI serves once critical startup completes: config resolution, auth setup, persistence wiring, and app-state initialization.
- Optional warmup happens in the background from `api/bootstrap.py`: planner/delegate LM construction, observability startup, and any noncritical backend readiness work.
- `/ready` reflects critical readiness only; use `/api/v1/runtime/status` and `/api/v1/runtime/tests/*` to inspect optional service health.

## `integrations/`

| Path | Description |
| --- | --- |
| `config/` | App/env/runtime settings helpers and defaults. |
| `database/` | Database manager, SQLModel models, repository, and DB-facing types. |
| `local_store.py` | Local session/history/optimization persistence. |
| `mcp/` | FastMCP server surface. |
| `observability/` | PostHog and MLflow integrations plus trace/request-context helpers. |
| `daytona/` | Daytona interpreter backend, bridge/runtime helpers, diagnostics, and volume access. |

## Scaffold and Assets

| Path | Description |
| --- | --- |
| `scaffold/agents/` | Bundled agent prompt assets. |
| `scaffold/hooks/` | Bundled local hook assets. |
| `scaffold/skills/` | Bundled Codex/Claude skills exposed by `fleet-rlm init`. |
| `scaffold/teams/` | Bundled team/inbox templates. |
| `ui/dist/` | Packaged frontend assets for installed distributions. |

## Verification

The current layout was checked against the source tree with:

```bash
# from repo root
find src/fleet_rlm -maxdepth 2 -type d | sort
rg --files src/fleet_rlm
```
