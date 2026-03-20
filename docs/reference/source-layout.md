# Source Layout (`src/fleet_rlm`)

This document reflects the current backend package structure in
`src/fleet_rlm/`. Paths below are relative to that directory.

## Top-Level Entry Points and Compatibility Facades

| Path | Description |
| --- | --- |
| `__init__.py` | Public package exports, version marker, and lazy compatibility re-exports. |
| `runners.py` | Compatibility wrapper that re-exports `fleet_rlm.cli.runners`. |
| `scaffold.py` | Compatibility wrapper that re-exports `fleet_rlm.utils.scaffold`. |
| `analytics/` | Compatibility package that re-exports `features.analytics` for older imports. |
| `AGENTS.md` | Package-scoped contributor guidance for backend work. |
| `py.typed` | Marker file for typed distributions. |

## CLI (`cli/`)

The canonical command surfaces live under `cli/`.

| Path | Description |
| --- | --- |
| `main.py` | Entry point for the lightweight `fleet` launcher. |
| `fleet_cli.py` | Typer-based `fleet-rlm` command surface. |
| `runners.py` | Shared runner helpers used by CLI, MCP, and tests. |
| `commands/init_cmd.py` | `fleet-rlm init` registration and scaffold install flow. |
| `commands/serve_cmds.py` | `serve-api` and `serve-mcp` registration. |

## API (`api/`)

The FastAPI server surface lives under `api/`.

| Path | Description |
| --- | --- |
| `main.py` | FastAPI app factory, lifespan management, and SPA mounting. |
| `config.py` | `ServerRuntimeConfig` for the HTTP/WebSocket server. |
| `dependencies.py` | Shared `ServerState` container and dependency helpers. |
| `middleware.py` | ASGI middleware registration. |
| `server_utils.py` | Shared API utility helpers. |

### Auth (`api/auth/`)

| Path | Description |
| --- | --- |
| `base.py` | Auth provider interfaces. |
| `factory.py` | Auth provider selection. |
| `dev.py` | Development/debug auth flow. |
| `entra.py` | Microsoft Entra bearer-token auth flow. |
| `admission.py` | Tenant admission helpers. |
| `types.py` | Shared auth datatypes. |

### Routers (`api/routers/`)

| Path | Description |
| --- | --- |
| `health.py` | `/health` and `/ready`. |
| `auth.py` | `GET /api/v1/auth/me`. |
| `runtime.py` | Runtime settings, diagnostics, and volume-backed helpers. |
| `sessions.py` | `GET /api/v1/sessions/state`. |
| `traces.py` | Trace feedback endpoints. |
| `ws/` | Shared chat/execution websocket runtime. |

### WebSocket Runtime (`api/routers/ws/`)

| Path | Description |
| --- | --- |
| `api.py` | WebSocket entrypoints. |
| `chat_runtime.py` | Per-connection runtime bootstrap. |
| `message_loop.py` | Frame parsing and dispatch. |
| `streaming.py` | Turn execution and event streaming. |
| `lifecycle.py` | Session and turn lifecycle events. |
| `session.py` | Session management helpers. |
| `session_store.py` | In-memory/persistence-backed session storage. |
| `commands.py` | Command frame routing. |
| `runtime_options.py` | Runtime mode and request option normalization. |
| `contracts.py` | Request/response contract helpers. |
| `turn.py` | Shared turn execution helpers. |
| `chat_connection.py` | Connection wrapper helpers. |
| `helpers.py` | Miscellaneous websocket helpers. |
| `repl_hook.py` | REPL integration hooks. |

### Execution (`api/execution/`)

| Path | Description |
| --- | --- |
| `events.py` | Execution event emitter types and helpers. |
| `step_builder.py` | Execution-step assembly for traces. |
| `step_builder_extractors.py` | Extraction helpers for streamed activity. |
| `step_builder_mapping.py` | Mapping helpers for persisted steps. |
| `sanitizer.py` | Payload cleanup and size guards. |

### Schemas (`api/schemas/`)

| Path | Description |
| --- | --- |
| `base.py` | Shared schema base helpers. |
| `core.py` | Core runtime request/response models. |
| `session.py` | Session-related schema models. |
| `task.py` | Task/workbench schema models. |

## Core Runtime (`core/`)

The runtime engine is split across agent orchestration, execution plumbing,
tool surfaces, and shared models.

| Path | Description |
| --- | --- |
| `config.py` | Planner/delegate LM bootstrap from env. |
| `interpreter.py` | Compatibility import surface for the interpreter. |
| `agent/` | Chat agent, signatures, memory, command dispatch, and recursive delegation. |
| `execution/` | Interpreter implementation, streaming helpers, runtime factory, and execution profiles. |
| `models/` | Shared runtime/streaming models. |
| `tools/` | Tool implementations exposed to the RLM runtime. |

### Agent (`core/agent/`)

| Path | Description |
| --- | --- |
| `chat_agent.py` | `RLMReActChatAgent` and `RLMReActChatSignature`. |
| `rlm_agent.py` | Recursive child-RLM delegation helpers. |
| `signatures.py` | Long-context, grounding, logs, and memory signatures. |
| `commands.py` | Shared command dispatch. |
| `memory.py` | Core memory mixins/helpers. |
| `session_history.py` | Chat/session history helpers. |
| `tool_delegation.py` | Tool delegation plumbing. |
| `trajectory_errors.py` | Trajectory error helpers. |

### Execution (`core/execution/`)

| Path | Description |
| --- | --- |
| `interpreter.py` | `ModalInterpreter` implementation. |
| `core_driver.py` | Core sandbox driver entrypoint. |
| `driver_factories.py` | Driver construction helpers. |
| `runtime_factory.py` | Runtime assembly helpers. |
| `streaming.py` | Stream-event generation and status helpers. |
| `streaming_context.py` | Streaming context state. |
| `streaming_citations.py` | Citation shaping helpers. |
| `document_cache.py` | Runtime document cache. |
| `document_sources.py` | Document source resolution. |
| `profiles.py` | Execution profile enum/helpers. |
| `validation.py` | Runtime output validation. |

### Tools (`core/tools/`)

| Path | Description |
| --- | --- |
| `document.py` | Document-analysis tool adapters. |
| `filesystem.py` | File and directory helpers. |
| `sandbox.py` | Sandbox execution tools. |
| `sandbox_helpers.py` | Sandbox helper utilities. |
| `sandbox_tools.py` | Sandbox-side helpers. |
| `delegate.py` | Recursive delegation tool surface. |
| `llm_tools.py` | LLM query helpers. |
| `chunking.py` | Chunking tool entrypoints. |
| `memory_intelligence.py` | Memory tree/audit/migration helpers. |
| `volume_ops.py` | Host-side volume operations. |
| `volume_tools.py` | Volume traversal/manipulation helpers. |
| `output_utils.py` | Output normalization utilities. |

## Infrastructure (`infrastructure/`)

Infrastructure packages provide configuration, persistence, providers, and MCP.

| Path | Description |
| --- | --- |
| `config/` | App config, env parsing, and runtime settings. |
| `database/` | `DatabaseManager`, SQLModel models, repository, and DB-facing types. |
| `mcp/` | FastMCP server surface. |
| `providers/daytona/` | Experimental Daytona interpreter backend, sandbox runtime, and thin chat wrapper. |
| `providers/modal/` | Modal provider helpers. |

## Features (`features/`)

Feature packages hold product-facing or reusable capabilities that are not the
core runtime loop itself.

| Path | Description |
| --- | --- |
| `analytics/` | PostHog and MLflow integrations plus trace sanitization. |
| `chunking/` | Header/size/timestamp/json-key chunking helpers. |
| `document_ingestion/` | Document ingestion entrypoints. |
| `logs/` | Execution-limit and logging helpers. |
| `scaffold/` | Bundled Claude Code skills, agents, hooks, and teams. |
| `terminal/` | Standalone terminal chat UI and settings helpers. |

## Supporting Packages and Assets

| Path | Description |
| --- | --- |
| `conf/` | Hydra config package and default config file. |
| `ui/` | Packaged frontend assets for installed distributions. |
| `utils/` | Modal helpers, regex helpers, scaffold installers, and small utilities. |
| `_scaffold/` | Legacy packaged scaffold assets kept for compatibility. |

## Verification

The current layout was checked against the source tree with:

```bash
# from repo root
find src/fleet_rlm -maxdepth 2 -type d | sort
rg --files src/fleet_rlm
```
