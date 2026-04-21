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
Preserved as-is:
- `daytona/` — sandbox runtime, interpreter, volumes
- `database/` — FleetRepository (async Postgres)
- `local_store.py` — SQLite sidecar
- `mcp/` — MCP server surface
- `observability/` — MLflow, PostHog

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
