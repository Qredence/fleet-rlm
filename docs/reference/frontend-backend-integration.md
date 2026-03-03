# Frontend ↔ Backend Integration

This document captures the current integration contract between:

- Frontend SPA: `src/frontend`
- Backend API: `src/fleet_rlm/server`

## API Base and Routing

Backend serves:

- Health: `/health`, `/ready`
- Versioned API: `/api/v1/*`
- WebSockets: `/api/v1/ws/chat`, `/api/v1/ws/execution`

## Backend Surfaces Used by Frontend

Primary interactive/chat surfaces:

- Canonical: `WS /api/v1/ws/chat`
- Observability: `WS /api/v1/ws/execution`

Runtime setup surfaces:

- `GET /api/v1/runtime/settings`
- `PATCH /api/v1/runtime/settings` (local-only writes)
- `POST /api/v1/runtime/tests/modal`
- `POST /api/v1/runtime/tests/lm`
- `GET /api/v1/runtime/status`

Runtime settings behavior:

- `PATCH /api/v1/runtime/settings` writes are local-only (`APP_ENV=local`).
- frontend runtime secret inputs are write-only; secrets are sent only when explicitly rotated or explicitly cleared.
- runtime model changes are hot-applied in-process and can be verified via:
  - `GET /api/v1/runtime/status`
  - `active_models.planner`
  - `active_models.delegate`
  - `active_models.delegate_small`

Deprecated/planned surfaces removed from backend:

- `/api/v1/chat`
- `/api/v1/tasks*`
- `/api/v1/sessions*` CRUD (state summary endpoint remains)
- `/api/v1/taxonomy*`
- `/api/v1/analytics*`
- `/api/v1/search`
- `/api/v1/memory*`
- `/api/v1/sandbox*`

## WebSocket Behavior

### `/api/v1/ws/chat`

- Accepts `message`, `cancel`, and `command` payloads.
- Emits `event`, `command_result`, and `error` envelopes.
- Auth claims are canonical tenant/user authority.

### Chat Trajectory Render Contract

Frontend chat rendering normalizes trajectory payloads from websocket events into
AI Elements components with deterministic ordering:

- supported step field aliases:
  - `tool_args` ↔ `input`
  - `observation` ↔ `output`
- supported payload forms:
  - structured `payload.step_data` with `payload.step_index`
  - indexed flat fields such as `thought_0`, `tool_name_0`, `tool_args_0`,
    `observation_0`
- rendering order:
  - sorted by step index (`0..N`)
  - per step sequence: `Reasoning` -> `Tool` -> `ChainOfThought`

Display policy:

- `Reasoning` shows thought text (auto-opens while streaming).
- `ToolInput` / `ToolOutput` render full structured payloads.
- `ChainOfThought` remains concise summary-only metadata (no raw JSON dumps).

### `/api/v1/ws/execution`

- Dedicated execution stream for artifact/step visualization.
- Filters by subscription identity (`workspace_id`, `user_id`, `session_id`).
- Emits `execution_started`, `execution_step`, `execution_completed`.
- `execution_step.step` now carries additive actor metadata:
  - `depth` (optional)
  - `actor_kind` (`root_rlm | sub_agent | delegate | unknown`, optional)
  - `actor_id` (optional)
  - `lane_key` (optional)

### Execution Graph Semantics

Artifact graph rendering maps execution steps into actor swimlanes:

- `Root RLM` lane: root planner/orchestrator execution.
- `Sub-agent` lanes: recursive/delegated agent depth contexts.
- `Delegate` lanes: delegate profile execution contexts.
- `Unknown` lane: fallback when actor hints are unavailable.

Ordering and edge rules:

- Step order is deterministic by `(timestamp, id)`.
- Parent-child edges are causal (primary).
- Chronological edges are dashed temporal hints (secondary).

Content policy:

- Graph, Timeline, and Preview surfaces do not intentionally truncate artifact
  text content.
- Large payloads may be shown in scrollable regions, but full text remains
  accessible in-place.

## Environment Variables

Frontend connectivity is typically driven by:

- `VITE_FLEET_API_URL`
- `VITE_FLEET_WS_URL`
- `VITE_FLEET_WORKSPACE_ID`
- `VITE_FLEET_USER_ID`
- `VITE_FLEET_TRACE`

Execution stream payload-size controls (backend):

- `WS_EXECUTION_MAX_TEXT_CHARS` (default `65536`)
- `WS_EXECUTION_MAX_COLLECTION_ITEMS` (default `500`)
- `WS_EXECUTION_MAX_RECURSION_DEPTH` (default `12`)

## Validation Checklist

From repo root:

```bash
uv run fleet-rlm serve-api --port 8000
rg -n "^  /" openapi.yaml
rg -n "@router.websocket" src/fleet_rlm/server/routers/ws/api.py
```

From `src/frontend` (optional frontend validation):

```bash
bun install --frozen-lockfile
bun run check
```

## Change Policy

If backend routes or payload shapes change, update this file in the same PR as the code change.

## Frontend API Layer Policy

- Canonical backend contracts for runtime/chat/auth should use `src/frontend/src/lib/rlm-api/*`.
- Legacy `src/frontend/src/lib/api` auth/chat endpoint helpers have been removed. Do not reintroduce auth/chat contracts in that layer.
