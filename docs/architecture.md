# Architecture Overview

`fleet-rlm` is a Daytona-backed recursive runtime wrapped by a thin transport shell and a narrow hosted-policy layer.

## Design Principles

Three choices drive the shape of this codebase. They are intentional, not accidents of scope:

1. **The backend is intentionally thin.** The Python layer is a transport + orchestration shell over `dspy.ReAct`, `dspy.RLM`, and Daytona sandboxes. "Intelligence" lives in DSPy (upstream) and in how recursive child sandboxes are scheduled (this repo). Expect to find plumbing in `src/fleet_rlm/api/` and policy in `src/fleet_rlm/runtime/` — not business logic mixed into request handlers.

2. **The UI is treated as core, not peripheral.** The runtime emits streaming events, code-execution results, and artifacts that only make sense in an interactive surface. Hiding them behind CLI-only access would throw away most of the runtime's observability. That is why `src/frontend/` is comparable in line count to `src/fleet_rlm/` — the UI is surfacing work the runtime does, not duplicating it.

3. **Two agent layers, both `dspy.*`, both real.**
   - **Chat surface:** `dspy.ReAct` at `src/fleet_rlm/runtime/agent/agent.py` handles turn-taking, tool dispatch, and the user-visible conversation loop.
   - **Recursive engine:** `dspy.RLM` is assembled through `src/fleet_rlm/runtime/modules/factory.py` and the module registry in `src/fleet_rlm/runtime/modules/registry.py` (with delegation at `src/fleet_rlm/runtime/tools/rlm_delegate.py`). Inputs cross into Daytona as sandbox-serializable payloads; sub-queries are dispatched recursively, bounded by `max_iterations` and `max_llm_calls`; sandboxes are isolated per delegation.

   The chat agent is the entry point; the recursive engine runs when a task exceeds what a single ReAct context can handle. Both use DSPy's module abstractions and share a single LLM-call budget across a recursive tree (see the `Recursive RLM isolation` section below).

## Current Layering

1. **Thin FastAPI/WebSocket transport** in `src/fleet_rlm/api/`
2. **Runtime core** in `src/fleet_rlm/runtime/` and `src/fleet_rlm/integrations/daytona/`
3. **Offline evaluation and optimization** in `src/fleet_rlm/quality/`

```mermaid
graph TB
    CLIENTS["CLI / Web UI"] --> API["FastAPI transport\napi/main.py\napi/routers/*\napi/runtime_services/*"]
    API --> RUNTIME["runtime/\nchat agent + execution helpers + modules"]
    RUNTIME --> DAYTONA["integrations/daytona/\ninterpreter + runtime + filesystem"]
    API --> EVENTS["api/events/\nexecution event shaping"]
    API --> PERSISTENCE["integrations/local_store.py\ndb/"]
    RUNTIME --> QUALITY["quality/\noffline GEPA + DSPy optimization"]
```

## What Each Layer Owns

### 1. Transport

Primary files:

- `src/fleet_rlm/api/main.py`
- `src/fleet_rlm/api/routers/ws/endpoint.py`
- `src/fleet_rlm/api/runtime_services/chat_runtime.py`
- `src/fleet_rlm/api/runtime_services/chat_persistence.py`
- `src/fleet_rlm/api/runtime_services/diagnostics.py`
- `src/fleet_rlm/api/runtime_services/settings.py`
- `src/fleet_rlm/api/runtime_services/volumes.py`

Responsibilities:

- App factory, lifespan, route mounting, and SPA asset serving
- Auth-derived HTTP and websocket identity
- Session lookup, runtime preparation, and service orchestration
- Websocket lifecycle and execution-event envelope delivery
- Runtime settings, diagnostics, and Daytona volume browsing

### 2. Runtime core

Primary files:

- `src/fleet_rlm/api/routers/ws/connection_loop.py`
- `src/fleet_rlm/api/routers/ws/turn_runner.py`
- `src/fleet_rlm/api/routers/ws/stream_loop.py`
- `src/fleet_rlm/runtime/factory.py`
- `src/fleet_rlm/runtime/agent/agent.py`
- `src/fleet_rlm/runtime/agent/runtime.py`
- `src/fleet_rlm/runtime/execution/*`
- `src/fleet_rlm/runtime/modules/*`

Responsibilities:

- Shared chat/runtime execution
- Recursive delegation and tool execution
- Execution-event assembly and workbench hydration inputs
- Runtime module assembly, registry management, escalation, and RLM routing

### 3. Daytona substrate

Primary files:

- `src/fleet_rlm/integrations/daytona/interpreter.py`
- `src/fleet_rlm/integrations/daytona/workspace_manager.py`
- `src/fleet_rlm/integrations/daytona/sandbox_executor.py`
- `src/fleet_rlm/integrations/daytona/isolation.py`
- `src/fleet_rlm/integrations/daytona/runtime.py`
- `src/fleet_rlm/integrations/daytona/session_runtime.py`
- `src/fleet_rlm/integrations/daytona/volumes.py`
- `src/fleet_rlm/integrations/daytona/_repo.py`
- `src/fleet_rlm/integrations/daytona/diagnostics.py`

Responsibilities:

- Public `DaytonaInterpreter` facade over typed workspace, execution, and child-delegation collaborators
- Sandbox and interpreter lifecycle
- Repo checkout, workspace path staging, and durable mounted volumes
- Provider-specific diagnostics and volume normalization
- Pydantic v2 normalization at workspace config/state boundaries; lightweight dataclasses/functions on execution hot paths

### Recursive RLM isolation

Recursive RLM work has two entry points:

- `delegate_to_rlm()` from the host ReAct tool registry
- `sub_rlm()` / `sub_rlm_batched()` from code running inside a `dspy.RLM`

Both entry points use `DaytonaInterpreter.build_delegate_child()` so child creation follows one backend-owned policy. The default policy is `RLM_CHILD_ISOLATION_MODE=auto`:

- if the parent has no durable mounted volume, fork the parent Daytona sandbox into a child sandbox;
- if a durable volume is mounted, create a clean child Daytona sandbox with the same repo/ref/context paths and a child-specific `volume_subpath`;
- if fork creation fails and `RLM_CHILD_FORK_FALLBACK=clean`, retry with a clean child sandbox;
- delete child sandboxes after each recursive task.

`RLM_CHILD_ISOLATION_MODE=context` is retained only as a backend/local debugging opt-out. It preserves the previous same-sandbox fresh-context behavior and should not be treated as the production isolation contract. Child outputs return through the RLM answer; child files and artifacts are not promoted to the parent automatically.

When the parent turn is analyzing a local host checkout and no `repo_url` is available to recreate that checkout in a clean child sandbox, `delegate_to_rlm()` writes a bounded text snapshot of relevant local repository files into the child sandbox under `artifacts/rlm-inputs/local_workspace_snapshot.txt` and adds that path to the child context. This preserves child sandbox isolation while giving the child enough explicit evidence to inspect local code.

Sandbox code can call `llm_query()`, `llm_query_batched()`, `sub_rlm()`, and `sub_rlm_batched()` through the Daytona bridge. These callbacks dispatch to Fleet's interpreter methods, not DSPy's per-forward injected counters, so `rlm_max_llm_calls` is one shared semantic-call budget across a recursive tree. `sub_rlm_batched()` keeps the runtime parallelism cap at 4 while sharing that same budget across sibling children.

### Stateful restore

Session manifests on durable storage are the authoritative local restart-restore source. The manifest `state` payload restores:

- `dspy.History` conversation turns;
- `AgentRuntime` core memory, applied as default core memory plus persisted keys;
- session-local loaded document paths;
- Daytona interpreter state, including sandbox ID, workspace path, repo/ref/context paths, volume name, and volume subpath.

Importing a session replaces session-local memory and document state instead of merging into the currently active runtime. Empty or missing state resets history, core memory, loaded documents, and sandbox buffers so switching sessions cannot leak stale agent context.

### 4. Offline quality

Primary files:

- `src/fleet_rlm/quality/*`

Responsibilities:

- DSPy evaluation
- GEPA optimization
- Offline scoring, datasets, and module registry management

## Canonical Runtime Surfaces

- `/health`
- `/ready`
- `GET /api/v1/auth/me`
- `POST /api/v1/auth/ws-ticket`
- `GET /api/v1/info`
- `GET/PATCH /api/v1/runtime/settings`
- `POST /api/v1/runtime/tests/daytona`
- `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`
- `GET /api/v1/runtime/volume/*`
- `GET/POST/PATCH/DELETE /api/v1/runtime/llm-profiles*`
- `GET/PATCH /api/v1/runtime/llm-roles`
- `GET/PATCH/DELETE /api/v1/sessions/{id}`
- `GET /api/v1/sessions`, `/state`, `/{id}/turns`, `/{id}/stats`, `/{id}/traces`, and `/{id}/trace-debug`
- `POST /api/v1/sessions/{id}/restore`, `/export`, and `/trace-export`
- `GET /api/v1/sandboxes`
- `GET/DELETE /api/v1/sandboxes/{id}`
- `POST /api/v1/sandboxes/{id}/archive`
- `GET /api/v1/runs/{run_id}/steps`
- `GET /api/v1/optimization/status`
- `GET /api/v1/optimization/modules`
- `POST /api/v1/optimization/run`
- `GET/POST /api/v1/optimization/runs`
- `GET /api/v1/optimization/runs/compare`
- `GET /api/v1/optimization/runs/{run_id}`, `/details`, and `/results`
- `POST /api/v1/optimization/runs/{run_id}/promotion-drafts`
- `GET/POST /api/v1/optimization/datasets`
- `GET /api/v1/optimization/datasets/{dataset_id}`
- `POST /api/v1/optimization/transcript-datasets`
- `POST /api/v1/traces/feedback`
- `WS /api/v1/ws/execution`
- `WS /api/v1/ws/execution/events`

## Deep Dives

For execution-level detail on the runtime paths sketched above, see:

- [Agent Runtime Execution Flow](explanation/agent-runtime-execution-flow.md) — traces a single chat turn from the WebSocket layer through `AgentRuntime` and `EscalatingFleetModule` routing (CoT → ReAct → RLM), streaming, and post-turn operations.
- [Sandbox Execution Pipeline](reference/sandbox-execution-pipeline.md) — details the host ↔ Daytona sandbox interaction during code execution: tool binding, session acquisition, setup injection, the Tool Bridge, the `SUBMIT()` marker protocol, recursive delegation, and the nine synchronization points between host and sandbox.
- [DSPy Daytona Interpreter Boundary](reference/dspy-daytona-interpreter-boundary.md) — async execution model, `asyncio.to_thread` rationale, and RLM budget knobs.
- [Daytona Architecture](reference/daytona-architecture.md) — sandbox lifecycle, volumes, session continuity, and the persistent memory model.

## Reading Order

When you need the current backend story, start here:

1. `src/fleet_rlm/api/main.py`
2. `src/fleet_rlm/api/routers/ws/endpoint.py`
3. `src/fleet_rlm/api/routers/ws/connection_loop.py`
4. `src/fleet_rlm/runtime/factory.py`
5. `src/fleet_rlm/runtime/agent/agent.py`
6. `src/fleet_rlm/integrations/daytona/interpreter.py`
7. `src/fleet_rlm/integrations/daytona/runtime.py`

## Historical Note

Older transition notes may still mention `orchestration_app/` and `api/orchestration/`. Those labels are historical only and are intentionally absent from the current tree.
