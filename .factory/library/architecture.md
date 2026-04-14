# Architecture

How the DSPy runtime, Daytona provider path, and web product surface fit together.

**What belongs here:** high-level ownership boundaries, runtime flows, integration seams, and invariants.
**What does NOT belong here:** per-feature TODOs or temporary debugging notes.

---

## Product Shape

`fleet-rlm` is a Daytona-backed DSPy application with four supported product surfaces:
- **Workspace** (`/app/workspace`) — live chat + workbench with streaming trace/reasoning
- **History** (`/app/history`) — browsable session history with turn-by-turn replay
- **Volumes** (`/app/volumes`) — browse mounted durable storage
- **Optimization** (`/app/optimization`) — GEPA/MLflow-backed DSPy prompt optimization dashboard
- **Settings** — runtime settings, LM/Daytona configuration, optimization defaults

FastAPI serves health/readiness endpoints, runtime/optimization/session APIs, websocket execution, and the packaged (or dev-server) web UI.

## High-Level Layers

### Backend transport (`src/fleet_rlm/api`)
Owns the public HTTP/websocket contract, request validation, auth/identity normalization, runtime-settings routes, optimization routes, session history routes, and websocket event emission.

### Agent Framework outer host (`src/fleet_rlm/agent_host`)
Narrow but real Microsoft Agent Framework outer host around the worker seam. Owns hosted workflow construction, session/HITL/checkpoint coordination, terminal event completion policy, and HITL resolution.

**No transitional bridge layers** — `orchestration_app/` and `api/orchestration/` have been retired. All policy is in `agent_host/` directly.

### DSPy runtime (`src/fleet_rlm/runtime`)
Owns Signatures, `dspy.Module` composition, the ReAct chat agent, `dspy.RLM` runtime modules, streaming helpers, evaluation/optimization helpers, and tool orchestration.

### Daytona integration (`src/fleet_rlm/integrations/daytona`)
Owns sandbox/session/volume lifecycle, runtime preflight diagnostics, and provider-specific execution behavior beneath the shared runtime contract.

### Local persistence (`src/fleet_rlm/integrations/local_store.py`)
SQLite-backed sidecar for developer workflows. Tables: `chat_sessions`, `chat_turns`, `datasets`, `optimization_runs`, `evaluation_results`, `example_scores`. Used by the session history API and optimization result persistence.

### Frontend (`src/frontend/src`)
Four surfaces: workspace, history, volumes, optimization (+ settings). Component layers:
- `components/ui/` — shadcn/Base UI primitives
- `components/ai-elements/` — AI conversation components
- `components/product/` — app-owned reusable compositions (data-table, detail-drawer, score-badge, diff-viewer, file-preview, chart-sparkline, etc.)
- `features/<surface>/` — surface-specific feature modules
- `lib/workspace/` — backend event adapters, workbench state reducers (run-workbench-normalizers, run-workbench-hydration, run-workbench-adapter)
- `lib/rlm-api/` — REST and WS clients (includes `sessions.ts`, `optimization.ts`)

## Critical Flows

### Workspace execution flow
1. Frontend submits a message over `/api/v1/ws/execution`.
2. Backend prepares the shared chat runtime; `switch_orchestration_session()` in `agent_host/sessions.py` creates a `ChatSession` in SQLite and stores `db_session_id` in session_record.
3. `stream.py`'s `_resolve_session_target()` copies `db_session_id` to `agent._db_session_id`.
4. `RLMReActChatAgent` runs; each completed turn is persisted via `add_turn()` in `local_store`.
5. Streamed events emitted to websocket, adapted by frontend into transcript/workbench UI.

### Session history flow
1. Sidebar calls `GET /api/v1/sessions?limit=50` via TanStack Query (`sessionEndpoints.listSessions`).
2. `/app/history` route loads the same data with search/filter support.
3. Session detail page calls `GET /api/v1/sessions/{id}/turns` and renders turns using `ai-elements` conversation components.
4. Delete calls `DELETE /api/v1/sessions/{id}`.

### Optimization flow
1. Frontend renders 4-tab dashboard: Modules / Datasets / Runs / Compare.
2. User picks module + registered dataset, submits via `POST /api/v1/optimization/runs`.
3. Background thread runs GEPA — persists per-example scores + before/after prompt text in `evaluation_results`/`example_scores` tables.
4. `GET /api/v1/optimization/runs/{id}/results` returns per-example breakdown.
5. Compare tab calls a multi-run comparison endpoint.

## Invariants

- The public runtime contract remains Daytona-only.
- `orchestration_app/` and `api/orchestration/` are fully retired — all imports point at `agent_host.*` or `worker.*` directly.
- Frontend consumers do not need to know about backend refactor details.
- Session history surface (`/app/history`) is a first-class supported route — not retired.
- `components/product/*` components must not import from `screens/*`.
- Workspace runtime/state modules in `lib/workspace/*` must not depend on workspace UI modules.
- SQLite local_store is not safe for concurrent writes — tests that write to it must be serialized.
