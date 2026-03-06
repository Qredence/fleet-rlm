# Frontend ↔ Backend Integration

This document captures the current integration contract between:

- Frontend SPA: `src/frontend`
- Backend API: `src/fleet_rlm/server`

## Supported Frontend Product Surfaces

The live frontend shell supports only:

- `/app/workspace`
- `/app/volumes`
- `/app/settings`

Legacy `/app/taxonomy*`, `/app/skills*`, `/app/memory`, and `/app/analytics`
routes remain redirect-only compatibility entrypoints and are not active
product surfaces.

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

- `GET /api/v1/auth/me`
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

## Auth Contract

- Frontend SPA auth uses Microsoft Entra via MSAL Browser.
- Default frontend authority is `https://login.microsoftonline.com/organizations`.
- Frontend login/logout callback path is `/login`.
- The SPA requests `api://<api-app-client-id>/access_as_user`.
- The same acquired access token is reused for:
  - `Authorization: Bearer ...` on HTTP requests
  - `access_token` bootstrap on `WS /api/v1/ws/chat`
- `GET /api/v1/auth/me` is the frontend’s canonical identity bootstrap endpoint.
- In Entra mode, backend tenant admission is enforced against the Neon `tenants` table before runtime persistence starts.

## WebSocket Behavior

### `/api/v1/ws/chat`

- Accepts `message`, `cancel`, and `command` payloads.
- Emits `event`, `command_result`, and `error` envelopes.
- Auth claims are canonical tenant/user authority.

### Chat Trace Render Contract

Frontend chat trace rendering uses AI Elements components with a live-first
chronological policy:

- primary trace order:
  - websocket arrival order for live events
  - typical sequence: `Reasoning` -> `Tool`/`Sandbox` -> `Reasoning` -> ...
- primary row mapping:
  - `reasoning_step` -> `Reasoning`
  - `tool_call` / `tool_result` -> `Tool` (or `Sandbox` for REPL-like payloads)
  - `plan_update` / `rlm_executing` / `memory_update` -> `Task`
  - `status` -> low-emphasis status note row
- secondary summaries:
  - `ChainOfThought` and `Queue` are summary surfaces only (non-primary)
  - summaries never replace or reorder primary chronological rows

Trajectory payload handling:

- trajectory data is fallback/summary-oriented
- trajectory-derived interleaving is only used when live reasoning/tool events
  are absent for the turn

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

- Step order is deterministic by `sequence`, with `(timestamp, id)` as fallback.
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
- Canonical frontend feature ownership now lives in:
  - `src/frontend/src/features/rlm-workspace/*` for the live chat/runtime surface
  - `src/frontend/src/features/volumes/*` for the Modal Volume browser
  - `src/frontend/src/features/shell/*` for composed shell navigation widgets
