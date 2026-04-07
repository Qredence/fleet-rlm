# Source Layout (`src/fleet_rlm`)

This document reflects the current backend package structure in
`src/fleet_rlm/`. Paths below are relative to that directory.

## Top-Level Areas

| Path | Description |
| --- | --- |
| `__init__.py` | Minimal public package exports and version marker. |
| `AGENTS.md` | Backend-specific contributor guidance. |
| `api/` | FastAPI transport, auth, schemas, routers, and API-specific helpers. |
| `cli/` | `fleet` / `fleet-rlm` entrypoints, command registration, runtime factory helpers, and terminal UX. |
| `runtime/` | Shared agent loop, execution engine, content processing, tools, and runtime models. |
| `integrations/` | Config, database, MCP, observability, and provider backends. |
| `scaffold/` | Bundled init assets: skills, agents, hooks, and team inboxes. |
| `ui/` | Packaged frontend build assets used by installed distributions. |
| `utils/` | Small shared helpers. |

## CLI (`cli/`)

| Path | Description |
| --- | --- |
| `main.py` | Entry point for the lightweight `fleet` launcher. |
| `fleet_cli.py` | Typer-based `fleet-rlm` command surface. |
| `runners.py` | Shared runner helpers used by CLI, MCP, API bootstrap, and tests. |
| `runtime_factory.py` | Shared runtime config assembly for API and MCP surfaces. |
| `commands/init_cmd.py` | `fleet-rlm init` registration and scaffold install flow. |
| `commands/serve_cmds.py` | `serve-api` and `serve-mcp` registration. |
| `terminal/` | Terminal chat UX, settings persistence, and session rendering. |

## API (`api/`)

| Path | Description |
| --- | --- |
| `main.py` | FastAPI app factory, lifespan management, and SPA mounting. |
| `bootstrap.py` | Runtime bootstrap, critical persistence init, optional background warmup, and shutdown orchestration. |
| `config.py` | `ServerRuntimeConfig` for the HTTP/WebSocket server. |
| `dependencies.py` | Shared `ServerState` container and dependency helpers. |
| `server_utils.py` | Shared API utility helpers. |
| `auth/` | Auth providers, tenant admission, and auth types. |
| `execution/` | Execution-step/event assembly for the workbench and traces. |
| `routers/` | HTTP and websocket route handlers. |
| `runtime_services/` | Runtime settings, diagnostics/status, and volume browsing helpers. |
| `schemas/` | Request/response models shared across routes. |

### WebSocket Runtime (`api/routers/ws/`)

The websocket transport layer is intentionally split into focused modules rather
than one large chat loop.

| Path | Description |
| --- | --- |
| `endpoint.py` | `/ws/chat` and `/ws/execution` websocket entrypoints. |
| `stream.py` | Turn execution and stream emission. |
| `session.py` | Runtime preparation and session switching. |
| `commands.py` | Command-frame dispatch plus run lifecycle initialization. |
| `types.py` | Typed websocket contracts and Daytona request normalization. |
| `helpers.py` | Low-level websocket send/close/auth helpers. |
| `lifecycle.py` | Execution lifecycle manager, run persistence, and terminal error classification. |
| `messages.py`, `persistence.py`, `manifest.py`, `artifacts.py` | Message parsing plus durable session/workbench state updates. |
| `runtime.py`, `turn_setup.py`, `turn_lifecycle.py` | Runtime prep and per-turn setup/finalization helpers. |
| `errors.py`, `failures.py`, `loop_exit.py`, `task_control.py`, `terminal.py`, `completion.py`, `execution_support.py`, `hitl.py` | Focused helpers for failure handling, cancellation, terminal ordering, execution summaries, and human-in-the-loop control. |

## Runtime (`runtime/`)

| Path | Description |
| --- | --- |
| `config.py` | Planner/delegate LM bootstrap from environment. |
| `__init__.py` | Lazy runtime export surface for `DaytonaInterpreter`, planner helpers, and `sandbox_driver`. |
| `agent/` | Shared DSPy orchestration, chat/session state, delegation policy, memory, and command helpers. |
| `execution/` | Interpreter implementation, streaming helpers, runtime factory, and execution profiles. |
| `content/` | Chunking, document ingestion, and execution-log processing helpers. |
| `models/` | Shared runtime/streaming models plus DSPy runtime module assembly. |
| `tools/` | Typed tool adapters exposed to the shared ReAct/RLM runtime. |

### Runtime Warmup Policy

- FastAPI serves once critical startup completes: config resolution, auth setup, persistence wiring, and app state initialization.
- Optional warmup happens in the background from `api/bootstrap.py`: planner/delegate LM construction, PostHog startup, and MLflow runtime startup.
- `/ready` reflects critical readiness only; use `/api/v1/runtime/status` and `/api/v1/runtime/tests/*` to inspect optional service health.

## Integrations (`integrations/`)

| Path | Description |
| --- | --- |
| `config/` | App/env/runtime settings helpers and defaults. |
| `database/` | `DatabaseManager`, SQLModel models, repository, and DB-facing types. |
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
