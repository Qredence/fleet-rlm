# fleet-rlm Codebase Map

This document is the current package-level architecture map for
`src/fleet_rlm`. It replaces the older backend map that still referred to the
pre-refactor `react/`, `server/`, and `db/` layout.

Use this alongside:

- [Source Layout](source-layout.md) for a file-by-file inventory
- [Python Backend Module Map](module-map.md) for dependency diagrams
- [Frontend and Backend Integration](frontend-backend-integration.md) for the
  web contract

## Reading Guide

- `runtime-critical`: part of the live execution path for agent turns
- `service-surface`: user or external entrypoint that exposes the runtime
- `support-layer`: shared config, compatibility facade, or pure utility
- `developer-tooling`: packaged assets or install-time helpers

## Top-Level Package Inventory

| Path | Role | Classification |
| --- | --- | --- |
| `src/fleet_rlm/__init__.py` | Public package exports, version marker, lazy compatibility re-exports | support-layer |
| `src/fleet_rlm/cli/` | Canonical CLI surfaces for `fleet` and `fleet-rlm` | service-surface |
| `src/fleet_rlm/api/` | FastAPI app factory, auth, HTTP routers, websocket runtime | runtime-critical |
| `src/fleet_rlm/core/` | Agent orchestration, execution runtime, tools, streaming, models | runtime-critical |
| `src/fleet_rlm/infrastructure/` | Config, database, MCP server, provider integrations | runtime-critical |
| `src/fleet_rlm/features/` | Analytics, terminal UX, chunking, scaffold assets, ingestion helpers | support-layer |
| `src/fleet_rlm/ui/` | Packaged frontend assets for installed distributions | service-surface |
| `src/fleet_rlm/utils/` | Shared helpers for modal/scaffold/regex/tool plumbing | support-layer |
| `src/fleet_rlm/conf/` | Hydra config package and defaults | support-layer |
| `src/fleet_rlm/daytona_rlm/` | CLI-facing Daytona pilot compatibility module | service-surface |
| `src/fleet_rlm/analytics/` | Compatibility package for legacy analytics imports | support-layer |
| `src/fleet_rlm/runners.py` | Compatibility wrapper over `fleet_rlm.cli.runners` | support-layer |
| `src/fleet_rlm/scaffold.py` | Compatibility wrapper over `fleet_rlm.utils.scaffold` | support-layer |
| `src/fleet_rlm/_scaffold/` | Legacy packaged scaffold assets kept for compatibility | developer-tooling |

## System Surfaces at a Glance

### `fleet`

- Entry: `src/fleet_rlm/cli/main.py`
- Primary role: lightweight launcher for terminal chat and `fleet web`
- Delegates to:
  - `src/fleet_rlm/features/terminal/chat.py` for terminal chat
  - `src/fleet_rlm/cli/fleet_cli.py` when launched as `fleet web`

### `fleet-rlm`

- Entry: `src/fleet_rlm/cli/fleet_cli.py`
- Primary role: Typer-based command surface for chat, API, MCP, scaffold, and
  Daytona flows
- Registered surfaces:
  - `chat`
  - `serve-api`
  - `serve-mcp`
  - `init`
  - `daytona-smoke`
  - `daytona-rlm`

### FastAPI server

- Entry: `src/fleet_rlm/api/main.py:create_app`
- Responsibilities:
  - initialize auth, persistence, planner/delegate LMs, and analytics
  - register HTTP + websocket routers
  - mount the built frontend SPA when assets are present

### FastMCP server

- Entry: `src/fleet_rlm/infrastructure/mcp/server.py:create_mcp_server`
- Wraps shared runners and exposes tool-call surfaces such as `chat_turn`,
  `analyze_long_document`, `grounded_answer`, and memory helpers

### Packaged SPA

- Packaged assets live in `src/fleet_rlm/ui/dist`
- `api/main.py:_resolve_ui_dist_dir` prefers a local `src/frontend/dist` build
  in source checkouts and falls back to packaged assets for installed wheels

## Directory-by-Directory Map

### `src/fleet_rlm/core/` (`runtime-critical`)

**Purpose**

- Own the shared runtime engine: chat agent, recursive delegation, streaming,
  execution profiles, tool surfaces, and interpreter plumbing.

**Key subpackages**

- `agent/`
  - `chat_agent.py`: `RLMReActChatAgent` and `RLMReActChatSignature`
  - `rlm_agent.py`: recursive child-RLM delegation helpers
  - `signatures.py`: DSPy signatures for long-context, grounding, logs, and
    memory operations
  - `memory.py`, `commands.py`, `session_history.py`: chat/session support
- `execution/`
  - `interpreter.py`: `ModalInterpreter`
  - `core_driver.py`, `driver_factories.py`: execution driver plumbing
  - `streaming.py`, `streaming_context.py`, `streaming_citations.py`:
    stream-event shaping
  - `runtime_factory.py`, `validation.py`, `profiles.py`: runtime assembly and
    guardrails
- `tools/`
  - `document.py`, `filesystem.py`, `sandbox.py`, `delegate.py`,
    `memory_intelligence.py`, `chunking.py`, `llm_tools.py`,
    `volume_ops.py`, `volume_tools.py`
- `models/`
  - `streaming.py`, `rlm_runtime_modules.py`

**Important dependency boundaries**

- Incoming: `api/routers/ws/*`, `cli/runners.py`, `features/terminal/chat.py`,
  `infrastructure/mcp/server.py`
- Outgoing: `features/chunking/*`, `features/document_ingestion/*`,
  `infrastructure/providers/daytona/*`, `infrastructure/config/*`

### `src/fleet_rlm/api/` (`runtime-critical`)

**Purpose**

- Own the HTTP/WebSocket product surface, auth, runtime option normalization,
  session persistence wiring, and execution-step emission.

**Key subpackages**

- `main.py`, `config.py`, `dependencies.py`, `middleware.py`
- `auth/`
  - dev and Entra auth providers plus tenant admission helpers
- `routers/`
  - `health.py`, `auth.py`, `runtime.py`, `sessions.py`, `traces.py`
  - `ws/` for the shared workspace websocket runtime
- `execution/`
  - execution event emitter, sanitizer, and step builder
- `schemas/`
  - core/session/task request-response models

**Important dependency boundaries**

- Incoming: `cli/commands/serve_cmds.py`, `cli/main.py` via `fleet web`
- Outgoing: `core/`, `infrastructure/database/`, `features/analytics/`,
  `infrastructure/providers/daytona/`, packaged `ui/`

### `src/fleet_rlm/infrastructure/` (`runtime-critical`)

**Purpose**

- Provide environment/config resolution, persistence, provider integrations, and
  the MCP server surface.

**Key subpackages**

- `config/`
  - `env.py`, `runtime_settings.py`, `_env_utils.py`
- `database/`
  - `engine.py`: `DatabaseManager`
  - `models.py`: SQLModel schema
  - `repository.py`: `FleetRepository`
  - `types.py`: DB-facing types
- `mcp/`
  - `server.py`: FastMCP server surface
- `providers/daytona/`
  - `chat_agent.py`, `runner.py`, `dspy_modules.py`, `config.py`, `protocol.py`
- `providers/modal/`
  - Modal provider helpers

**Important dependency boundaries**

- Incoming: `api/`, `cli/commands/serve_cmds.py`, `core/execution/*`
- Outgoing: Postgres/Neon, FastMCP, Daytona APIs, env/runtime settings

### `src/fleet_rlm/features/` (`support-layer`)

**Purpose**

- Hold product-facing feature packages that are reused by multiple surfaces but
  are not themselves the canonical runtime loop.

**Key subpackages**

- `analytics/`
  - PostHog lifecycle, MLflow tracing, trace context, sanitization
- `chunking/`
  - header, size, timestamp, and JSON-key chunking helpers
- `document_ingestion/`
  - ingestion entrypoints
- `logs/`
  - execution-limit and logging helpers
- `terminal/`
  - standalone terminal chat UI, slash-command plumbing, local settings
- `scaffold/`
  - packaged skills, agents, hooks, and team config

### `src/fleet_rlm/cli/` (`service-surface`)

**Purpose**

- Keep user-facing command surfaces thin while sharing config and runner
  assembly.

**Important files**

- `main.py`: `fleet` command
- `fleet_cli.py`: `fleet-rlm` Typer command surface
- `runners.py`: shared runner assembly
- `commands/init_cmd.py`, `commands/serve_cmds.py`: extracted command
  registration

**Important dependency boundaries**

- Incoming: package entry points, tests
- Outgoing: `api/`, `core/`, `infrastructure/mcp/`, `features/terminal/`,
  `utils/scaffold.py`

### Compatibility, assets, and utilities

| Path | Why it exists |
| --- | --- |
| `src/fleet_rlm/analytics/` | Keeps `fleet_rlm.analytics` imports working while real code lives in `features.analytics` |
| `src/fleet_rlm/runners.py` | Keeps `fleet_rlm.runners` imports working while canonical code lives in `cli.runners` |
| `src/fleet_rlm/scaffold.py` | Keeps `fleet_rlm.scaffold` imports working while canonical code lives in `utils.scaffold` |
| `src/fleet_rlm/daytona_rlm/` | CLI-facing Daytona pilot compatibility surface |
| `src/fleet_rlm/ui/` | Packaged frontend build artifacts |
| `src/fleet_rlm/utils/` | Small shared helpers used across CLI/runtime tooling |

## Cross-Cutting Runtime Boundaries

These are the boundaries that most often need to stay in sync:

1. Websocket contract
   `api/routers/ws/*`, `api/execution/*`, `core/execution/*`,
   `src/frontend/src/features/rlm-workspace/*`, and
   `src/frontend/src/stores/chatStore.ts`
2. Runtime mode split
   `api/schemas/core.py`, `api/routers/ws/runtime_options.py`,
   `infrastructure/providers/daytona/*`, and the frontend runtime settings flow
3. Persistence and trace shaping
   `infrastructure/database/*`, `api/execution/*`, and `/api/v1/sessions/state`
4. CLI/runtime assembly
   `cli/fleet_cli.py`, `cli/commands/serve_cmds.py`, `api/main.py`,
   `infrastructure/mcp/server.py`, and compatibility wrappers in
   `runners.py`/`scaffold.py`

## Simplification Hotspots

These are the areas most likely to create drift or make future changes
expensive:

### 1. Websocket runtime fan-out

The shared workspace flow spans `api/routers/ws/*`, `api/execution/*`,
`core/agent/*`, and `core/execution/*`. Small contract changes often cascade
across transport, streaming, persistence, and frontend rendering.

### 2. Compatibility facades

`fleet_rlm.analytics`, `fleet_rlm.runners`, and `fleet_rlm.scaffold` are useful
for backwards compatibility, but they make it easier for docs or imports to
reference the wrong "real" location.

### 3. Daytona integration surface

The Daytona pilot is intentionally experimental, but it still shares the main
websocket workspace. Changes to `runtime_mode`, source controls, or workbench
behavior need synchronized updates across backend schemas, runtime options, and
frontend UX.

### 4. Packaged vs source-built frontend assets

`fleet web` prefers `src/frontend/dist` in source checkouts and
`src/fleet_rlm/ui/dist` in packaged installs. Docs and release checks need to
stay explicit about that split so local source workflows do not drift from
published wheel behavior.

## Verification

The current package map was checked against the source tree with:

```bash
# from repo root
find src/fleet_rlm -maxdepth 2 -type d | sort
rg --files src/fleet_rlm
```

Last updated: 2026-03-17
