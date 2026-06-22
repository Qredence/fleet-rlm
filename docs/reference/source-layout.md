# Source Layout (`src/fleet_rlm`)

This document reflects the current backend package structure in `src/fleet_rlm/`. Paths below are relative to that directory.

## Top-Level Areas

| Path | Description |
| --- | --- |
| `__init__.py` | Minimal public package exports and version marker. |
| `AGENTS.md` | Backend-specific contributor guidance. |
| `api/` | FastAPI transport, auth, schemas, routers, websocket lifecycle, runtime services, and event shaping. |
| `cli/` | `fleet` / `fleet-rlm` entrypoints, command registration, runtime helpers, and terminal UX. |
| `integrations/` | Config, database, observability, Daytona, and local-store integrations. |
| `runtime/` | Shared agent loop, execution helpers, content processing, tools, runtime modules, and runtime models. |
| `quality/` | Offline DSPy evaluation and GEPA optimization. |
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
| `routers/info.py` | API metadata endpoints. |
| `routers/llm_profiles.py` | Local LLM profile and role management routes. |
| `routers/optimization/` | GEPA optimization status, run, and dataset routes. |
| `routers/runs.py` | Execution run-step lookup routes. |
| `routers/sandboxes.py` | Daytona sandbox listing, detail, delete, and archive routes. |
| `routers/sessions.py` | Session state, history, transcript, archive, and export routes. |
| `routers/traces.py` | Feedback and trace-reporting routes. |
| `routers/ws/` | Websocket transport for execution and execution-event subscriptions. |

### WebSocket runtime (`api/routers/ws/`)

| Path | Description |
| --- | --- |
| `endpoint.py` | `/api/v1/ws/execution` and `/api/v1/ws/execution/events` entrypoints. |
| `connection_loop.py` | Connection-scoped conversational websocket loop and in-flight task control. |
| `turn_setup.py` | Converts a websocket payload into a prepared runtime turn and session context. |
| `turn_runner.py` | Runs one prepared turn, emits terminal events, and persists completion/failure. |
| `stream_loop.py` | Runtime event iteration and websocket delivery. |
| `stream_events.py` | Runtime-event serialization and terminal semantics. |
| `stream_summary.py` | Completion summary and workbench hydration payload assembly. |
| `commands.py` | Command-frame dispatch and run lifecycle initialization. |
| `transport.py` | Authentication, ticket handling, message parsing, close/send helpers, and startup errors. |
| `session.py` | Session id normalization and cache helpers. |
| `repl_bridge.py` | Interpreter callback forwarding into execution-event flow. |
| `artifacts.py` | Artifact event helpers. |

## `runtime/`

| Path | Description |
| --- | --- |
| `config.py` | Planner/delegate LM bootstrap from environment. |
| `agent/` | Shared DSPy orchestration, chat/session state, delegation policy, memory, and command helpers. |
| `execution/` | Interpreter support, streaming helpers, runtime factory glue, and execution profiles. |
| `content/` | Chunking, ingestion, and execution-log processing helpers. |
| `modules/` | DSPy module assembly, escalation, workspace phases, module registry, and prompt/routing helpers. |
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
| `observability/` | PostHog and MLflow integrations plus trace/request-context helpers. |
| `daytona/` | Daytona interpreter facade, workspace/session manager, sandbox executor, child delegation, bridge/runtime helpers, diagnostics, and volume access. |

## Packaged Assets

| Path | Description |
| --- | --- |
| `ui/dist/` | Packaged frontend assets for installed distributions. |

## Verification

The current layout was checked against the source tree with:

```bash
# from repo root
find src/fleet_rlm -maxdepth 2 -type d | sort
rg --files src/fleet_rlm
```
