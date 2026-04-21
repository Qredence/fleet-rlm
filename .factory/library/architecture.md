# Architecture

## System Overview

fleet-rlm is being simplified from a 4-layer to a 2-layer architecture:

**Before (4 layers):**
```
Transport Shell (FastAPI/WS) → Agent Framework Host → Worker Boundary → Recursive Runtime (DSPy)
```

**After (2 layers):**
```
Transport Shell (FastAPI/WS) → DSPy ReAct Agent
```

## Components

### 1. Transport Shell (`api/`)
FastAPI application with WebSocket and HTTP endpoints. Handles auth, routing, SPA serving.
- `api/main.py` — app factory, lifespan, route mounting
- `api/routers/ws/` — WebSocket endpoint for chat streaming
- `api/routers/` — HTTP routers (sessions, optimization, auth, health, runtime, traces)
- `api/runtime_services/` — orchestration helpers for chat, persistence, diagnostics

### 2. DSPy ReAct Agent (`runtime/agent/`)
Single `dspy.Module` wrapping `dspy.ReAct` with tools.
- `agent.py` — the module class (dspy.Module subclass with dspy.ReAct)
- `runtime.py` — AgentRuntime: manages interpreter, history, tools, core memory
- `chat.py` — ChatOrchestrator: sync/async chat turns, streaming
- `chat_session_state.py` — session state with dspy.History
- `signatures.py` — DSPy signatures (RLMReActChatSignature etc.)

### 3. Tool Registry (`runtime/tools/`)
Plain callables discovered via directory scan, passed to dspy.ReAct.
- Categories: sandbox, filesystem, document, chunking, buffer/volume, core memory, RLM delegation
- Plugin scan: `discover_tools()` scans `runtime/tools/*.py` for tool functions

### 4. Integrations (`integrations/`)
External system integrations and persistence layer.

**Daytona (`daytona/`):**
- `interpreter.py` — `DaytonaInterpreter` (~1,750 lines). ReAct-compatible sandbox interpreter. Handles session lifecycle, code execution (direct and bridged), child delegation, and degradation tracking. **Target for decomposition into session_lifecycle, execution_bridge, delegation, and degradation modules.**
- `runtime.py` — `DaytonaSandboxRuntime` (factory) and `DaytonaSandboxSession` (per-sandbox handle). **Target for splitting session into lifecycle + filesystem concerns.**
- `runtime_helpers.py` — Daytona client building, volume state normalization, readiness polling
- `config.py` — Daytona env resolution and `ResolvedDaytonaConfig`
- `volumes.py` — Volume tree browsing and file reading
- `bridge.py` — HTTP broker for host-side tool callbacks from sandbox
- `diagnostics.py` — Structured error types and smoke test
- `async_compat.py` — Global async/sync compatibility bridge

**Database (`database/`):**
- `repository.py` — `FleetRepository` (~1,800 lines). Async DB access layer covering identity, chat, execution, optimization, memory, jobs, sandbox sessions. **Target for splitting into IdentityRepository, ChatRepository, OptimizationRepository, MemoryRepository.**
- `repository_shared.py` — `RepositoryContextMixin` with tenant/workspace resolution and Postgres request context setting
- `engine.py` — Async SQLAlchemy engine/session factory with Neon-specific URL normalization
- `types.py` — Frozen dataclass DTOs for repository requests
- `models_*.py` — SQLAlchemy ORM models (identity, jobs, memory, optimization, runs, sandbox)

**Observability (`observability/`):**
- `__init__.py` — Lazy-export facade using `__getattr__` to avoid loading PostHog/MLflow/DSPy at import time
- `mlflow_runtime.py` — MLflow initialization, DSPy callback registration, token usage extraction. **Target for consolidating token extraction and reducing private API usage.**
- `mlflow_traces.py` — Trace lookup, feedback logging, trace-to-dataset export
- `posthog_callback.py` — DSPy callback that emits `$ai_generation` events to PostHog
- `client.py` — Singleton PostHog client lifecycle
- `config.py` — Pydantic models for `PostHogConfig` and `MlflowConfig`

**MCP (`mcp/`):**
- `server.py` — FastMCP server exposing ReAct + RLM tools. **Target for deferring heavy imports.**

**Config (`config/`):**
- `env.py` — Pydantic-based `AppConfig` schema
- `runtime_settings.py` — Runtime setting definitions, .env path resolution, snapshot building
- `_env_utils.py` — Pure helpers for parsing env booleans, integers, CSV lists

**Local Store:**
- `local_store.py` — SQLite sidecar for local sessions, chat turns, datasets, optimization runs. Synchronous SQLModel with inline migrations.

### 5. Optimization (`runtime/quality/`)
Preserved as-is. DSPy evaluation and optimization workflows.

## Data Flows

### Chat Turn Flow (simplified)
1. WebSocket receives message frame
2. Transport constructs/retrieves session context
3. `runtime/factory.py` builds agent with tools
4. Agent runtime restores history from volume (if resuming) — **NOTE: as of milestone persistence-rlm, `restore_history_from_volume` is not auto-called; it must be explicitly invoked by the transport layer**
5. `dspy.ReAct` processes turn with tools
6. Events streamed back via WebSocket
7. History persisted to Daytona volume + metadata to DB — **NOTE: as of milestone persistence-rlm, `persist_history_to_volume` and `persist_session_metadata` are not auto-called from `AgentRuntime.chat_turn()`; they exist as standalone library helpers**

### RLM Delegation Flow
1. Agent decides to delegate via `delegate_to_rlm` tool
2. Tool creates/reuses Daytona sandbox
3. `dspy.RLM` executes in sandbox with sub-interpreter
4. Result returned to agent as tool output

**NOTE:** As of milestone persistence-rlm, `AgentRuntime.chat_turn()` does not call `set_delegate_interpreter()` before running the agent. If the LLM selects the `delegate_to_rlm` tool during a real chat turn, it will raise `RuntimeError`. The tool is correctly implemented and tested in isolation; the wiring gap in `AgentRuntime` is a known deferred task.

## Invariants

- Agent is always a `dspy.Module` (optimizer compatibility)
- History is always `dspy.History` (DSPy native)
- Tools are plain callables or `dspy.Tool` instances
- Session state persists to both volume (full state) and DB (metadata)
- No `agent_host/` or `worker/` imports anywhere in the codebase
