# Source Layout (`src/fleet_rlm`)

This document reflects the current package structure of the Fleet-RLM backend. All paths are relative to `src/fleet_rlm/`.

## Top-Level Package

| File | Description |
|------|-------------|
| `__init__.py` | Public exports and version |
| `cli.py` | `fleet-rlm` Typer CLI entrypoint |
| `fleet_cli.py` | `fleet` launcher entrypoint |
| `runners.py` | High-level runner functions |
| `config.py` | Core configuration |
| `execution_limits.py` | Execution constraints and timeouts |
| `logging.py` | Logging utilities |
| `_env_utils.py` | Environment variable utilities |
| `py.typed` | Marker file for PEP 561 type hints |

## Core Runtime (`core/`)

Host-side adapters and sandbox-side protocol/helpers for Modal execution.

| File | Description |
|------|-------------|
| `__init__.py` | Module exports |
| `config.py` | Interpreter configuration |
| `interpreter.py` | Modal sandbox interpreter |
| `driver.py` | Sandbox driver protocol |
| `driver_factories.py` | Driver factory functions |
| `llm_tools.py` | LLM tool adapters (host-side) |
| `sandbox_tools.py` | Sandbox tool implementations |
| `volume_ops.py` | Volume operations (host-side) |
| `volume_tools.py` | Volume tools (sandbox-side) |
| `session_history.py` | Session history management |
| `output_utils.py` | Output processing utilities |

## ReAct Agent Runtime (`react/`)

DSPy-based ReAct agent, tool registry, command dispatch, and streaming.

| File | Description |
|------|-------------|
| `__init__.py` | Module exports |
| `agent.py` | `RLMReActChatAgent` - primary chat runtime |
| `signatures.py` | DSPy signatures for agent workflows |
| `commands.py` | Command dispatch (exit, help, etc.) |
| `streaming.py` | Streaming event generation |
| `streaming_context.py` | Streaming context management |
| `streaming_citations.py` | Citation handling in streams |
| `delegate_sub_agent.py` | Child agent delegation logic |
| `tool_delegation.py` | Tool delegation utilities |
| `rlm_runtime_modules.py` | RLM runtime module collection |
| `runtime_factory.py` | Runtime factory functions |
| `core_memory.py` | Core memory management |
| `document_cache.py` | Document caching |
| `document_sources.py` | Document source handling |
| `validation.py` | Validation utilities |
| `trajectory_errors.py` | Trajectory error handling |

### Tools (`react/tools/`)

Agent tool implementations.

| File | Description |
|------|-------------|
| `__init__.py` | Tool registry exports |
| `chunking.py` | Text chunking tools |
| `delegate.py` | Agent delegation tools |
| `document.py` | Document processing tools |
| `filesystem.py` | Filesystem operations |
| `memory_intelligence.py` | Memory and RAG tools |
| `sandbox.py` | Sandbox execution tools |
| `sandbox_helpers.py` | Sandbox helper functions |

## Server (`server/`)

FastAPI application, HTTP/WebSocket routers, auth, and schemas.

| File | Description |
|------|-------------|
| `__init__.py` | Module exports |
| `main.py` | FastAPI app factory and lifespan |
| `config.py` | Server runtime config |
| `deps.py` | Shared dependencies and state |
| `middleware.py` | ASGI middleware |
| `runtime_settings.py` | Runtime settings API |
| `utils.py` | Server utilities |

### Routers (`server/routers/`)

HTTP and WebSocket endpoint handlers.

| File | Description |
|------|-------------|
| `__init__.py` | Router exports |
| `auth.py` | Authentication endpoints |
| `health.py` | Health check endpoints (`/health`, `/ready`) |
| `runtime.py` | Runtime settings endpoints |
| `sessions.py` | Session state endpoints |
| `traces.py` | Trace endpoints |

### WebSocket Routers (`server/routers/ws/`)

WebSocket runtime for chat and execution.

| File | Description |
|------|-------------|
| `__init__.py` | Router exports |
| `api.py` | WebSocket endpoint definitions |
| `chat_runtime.py` | Chat runtime handler |
| `chat_connection.py` | Connection management |
| `message_loop.py` | Message loop processing |
| `streaming.py` | Streaming event dispatch |
| `session.py` | WebSocket session handling |
| `session_store.py` | Session storage |
| `lifecycle.py` | Session lifecycle management |
| `turn.py` | Turn-based message handling |
| `commands.py` | WebSocket command handling |
| `helpers.py` | Helper functions |
| `repl_hook.py` | REPL integration |

### Auth (`server/auth/`)

Authentication abstraction with dev and Entra modes.

| File | Description |
|------|-------------|
| `__init__.py` | Auth module exports |
| `base.py` | Base auth interface |
| `types.py` | Auth-related types |
| `factory.py` | Auth provider factory |
| `dev.py` | Development auth (debug headers) |
| `entra.py` | Microsoft Entra ID auth |
| `admission.py` | Tenant admission logic |

### Schemas (`server/schemas/`)

Pydantic request/response models.

| File | Description |
|------|-------------|
| `__init__.py` | Schema exports |
| `base.py` | Base schema classes |
| `core.py` | Core API schemas |
| `session.py` | Session-related schemas |
| `task.py` | Task-related schemas |

### Execution (`server/execution/`)

Execution observability package.

| File | Description |
|------|-------------|
| `__init__.py` | Module exports |
| `events.py` | Execution event models |
| `sanitizer.py` | Output sanitization |
| `step_builder.py` | Step builder for traces |
| `step_builder_extractors.py` | Trace extractors |
| `step_builder_mapping.py` | Step mapping utilities |

## Database (`db/`)

Neon/Postgres engine, models, and repository.

| File | Description |
|------|-------------|
| `__init__.py` | Module exports |
| `engine.py` | Database engine and connection |
| `models.py` | SQLAlchemy model classes |
| `repository.py` | Data access layer |
| `types.py` | Custom database types |

## Analytics (`analytics/`)

MLflow and PostHog integration for observability.

| File | Description |
|------|-------------|
| `__init__.py` | Module exports |
| `config.py` | Analytics configuration |
| `client.py` | Analytics client |
| `mlflow_integration.py` | MLflow tracing integration |
| `mlflow_evaluation.py` | MLflow evaluation workflows |
| `mlflow_optimization.py` | DSPy optimization with MLflow |
| `posthog_callback.py` | PostHog telemetry callback |
| `scorers.py` | Evaluation scorers |
| `sanitization.py` | Data sanitization for traces |
| `trace_context.py` | Trace context management |

## MCP Server (`mcp/`)

Model Context Protocol server runtime.

| File | Description |
|------|-------------|
| `__init__.py` | Module exports |
| `server.py` | FastMCP server implementation |

## Models (`models/`)

Canonical streaming and event models shared across modules.

| File | Description |
|------|-------------|
| `__init__.py` | Model exports |
| `streaming.py` | Streaming event models |

## Chunking (`chunking/`)

Text chunking utilities for document processing.

| File | Description |
|------|-------------|
| `__init__.py` | Module exports |
| `headers.py` | Header-based chunking |
| `json_keys.py` | JSON key extraction |
| `size.py` | Size-based chunking |
| `timestamps.py` | Timestamp handling |

## CLI Commands (`cli_commands/`)

CLI command implementations.

| File | Description |
|------|-------------|
| `__init__.py` | Command exports |
| `init_cmd.py` | `fleet-rlm init` implementation |
| `serve_cmds.py` | `fleet-rlm serve-*` implementations |

## Terminal (`terminal/`)

Terminal UI components for interactive chat.

| File | Description |
|------|-------------|
| `__init__.py` | Module exports |
| `chat.py` | Terminal chat interface |
| `commands.py` | Terminal command handling |
| `settings.py` | Terminal settings |
| `ui.py` | UI rendering components |

## Configuration (`conf/`)

Configuration files.

| File | Description |
|------|-------------|
| `__init__.py` | Module marker |
| `config.yaml` | Default configuration |

## Utilities (`utils/`)

Shared utility modules.

| File | Description |
|------|-------------|
| `__init__.py` | Utility exports |
| `modal.py` | Modal helper utilities |
| `regex.py` | Regex extraction helpers |
| `scaffold.py` | Scaffolding utilities |
| `tools.py` | Tool-related utilities |

## UI Distribution (`ui/`)

Packaged frontend assets.

| Directory | Description |
|-----------|-------------|
| `dist/` | Built frontend assets |

## Scaffold Templates (`_scaffold/`)

Packaged templates installed by `fleet-rlm init`.

| Directory | Description |
|-----------|-------------|
| `skills/` | Skill templates (dspy-signature, modal-sandbox, rlm-*, etc.) |
| `agents/` | Agent templates (modal-interpreter, orchestrator, specialist, subcall) |
| `hooks/` | Hook templates for document processing, error handling |
| `teams/` | Team configuration templates |

### Skills (`_scaffold/skills/`)

| Directory | Description |
|-----------|-------------|
| `dspy-signature/` | DSPy signature skill template |
| `modal-sandbox/` | Modal sandbox skill template |
| `rlm/` | RLM skill template |
| `rlm-batch/` | RLM batch processing template |
| `rlm-debug/` | RLM debugging template |
| `rlm-execute/` | RLM execution template |
| `rlm-long-context/` | RLM long-context template |
| `rlm-memory/` | RLM memory template |
| `rlm-run/` | RLM run template |
| `rlm-test-suite/` | RLM test suite template |

### Agents (`_scaffold/agents/`)

| File | Description |
|------|-------------|
| `modal-interpreter-agent.md` | Modal interpreter agent template |
| `rlm-orchestrator.md` | RLM orchestrator agent template |
| `rlm-specialist.md` | RLM specialist agent template |
| `rlm-subcall.md` | RLM subcall agent template |

### Hooks (`_scaffold/hooks/`)

| File | Description |
|------|-------------|
| `README.md` | Hook documentation |
| `hookify.fleet-rlm-document-process.local.md` | Document processing hook |
| `hookify.fleet-rlm-large-file.local.md` | Large file handling hook |
| `hookify.fleet-rlm-llm-query-error.local.md` | LLM error handling hook |
| `hookify.fleet-rlm-modal-error.local.md` | Modal error handling hook |

## Frontend Workspace

- `src/frontend/`: React + TypeScript Web UI (separate package)
- `src/frontend/openapi/fleet-rlm.openapi.yaml`: Frontend API spec copy

## Root-Level Files

- `openapi.yaml`: Canonical API contract at repository root
- `migrations/`: Alembic migrations for Neon schema

## Import Boundaries

Suggested import conventions:

- **FastAPI-only code** lives under `server/`
- **Modal primitives** (`modal.*`) should remain in `core/` and narrowly-scoped helpers
- **DSPy signatures/modules** live under `react/`; server code can use `dspy.context(...)` but should avoid defining DSPy programs
- **Database access** goes through `db/repository.py`
- **Shared models** are in `models/` for cross-module use

## Notes

- All paths are relative to `src/fleet_rlm/`
- The canonical API contract is `openapi.yaml` at repository root
- Migration files are in `migrations/` at repository root
