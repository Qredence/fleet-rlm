# Frontend ↔ Backend Integration

This document captures the current integration contract between:

- Frontend SPA: `src/frontend`
- Backend API: `src/fleet_rlm/api`

## Supported Frontend Product Surfaces

The live frontend shell supports only:

- `/app/workspace`
- `/app/volumes`
- `/app/settings`

Retired `/app/taxonomy*`, `/app/skills*`, `/app/memory`, and `/app/analytics`
paths are no longer compatibility entrypoints; the frontend should route them
through the not-found flow instead of redirecting them into active product
surfaces.

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
- `POST /api/v1/runtime/tests/daytona`
- `GET /api/v1/runtime/status`

Runtime settings behavior:

- `PATCH /api/v1/runtime/settings` writes are local-only (`APP_ENV=local`).
- frontend runtime secret inputs are write-only; secrets are sent only when explicitly rotated or explicitly cleared.
- runtime model changes are hot-applied in-process and can be verified via:
  - `GET /api/v1/runtime/status`
  - `active_models.planner`
  - `active_models.delegate`
  - `active_models.delegate_small`
- `execution_mode` is Modal-only request state. Daytona requests do not send it.
- Daytona-specific source controls are `repo_url`, `repo_ref`,
  `context_paths`, and `batch_concurrency`.

Daytona SDK and volume behavior:

- The backend Daytona integration is implemented on the official Daytona Python SDK.
- `DAYTONA_TARGET` is treated as Daytona SDK config only; it is not used as a workspace id,
  sandbox id, or persistent volume name.
- The Daytona Volumes page path browses a workspace-scoped persistent Daytona volume whose name is
  derived from the authenticated workspace/tenant claim.
- That persistent volume is attached to temporary Daytona sandboxes through the Python SDK
  `volume.get(..., create=True)` + `VolumeMount(...)` flow.

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
- Browser token storage uses the canonical `fleet-rlm:access-token` key only.
  The legacy `fleet_access_token` localStorage migration path is no longer
  supported.
- Default frontend authority is `https://login.microsoftonline.com/organizations`.
- Frontend login/logout callback path is `/login`.
- The SPA requests `api://<api-app-client-id>/access_as_user`.
- The same acquired access token is reused for:
  - `Authorization: Bearer ...` on HTTP requests
  - `access_token` bootstrap on websocket clients where the server supports query-token compatibility; prefer this only when a header cannot be forwarded
- `GET /api/v1/auth/me` is the frontend’s canonical identity bootstrap endpoint.
- In Entra mode, backend tenant admission is enforced against the Neon `tenants` table before runtime persistence starts.

## WebSocket Behavior

### `/api/v1/ws/chat`

- Accepts `message`, `cancel`, and `command` payloads.
- Emits `event`, `command_result`, and `error` envelopes.
- Canonical purpose: conversational turn streaming only. The transcript should stay focused on
  user/assistant exchange plus lightweight live trace.
- Auth claims are canonical tenant/user authority.
- `session_id` is the only authoritative client-controlled selector for websocket binding.
- `workspace_id` and `user_id` are unsupported on websocket payloads and should be rejected immediately.
- `runtime_mode` selects the top-level runtime:
  - `modal_chat` for the default product path
  - `daytona_pilot` for the Daytona-backed variant of the shared ReAct + `dspy.RLM` workspace runtime
- Daytona `message` frames may also carry `repo_url`, `repo_ref`,
  `context_paths`, and `batch_concurrency`.
- Daytona requests reject request-side `max_depth`, and `repo_ref` requires
  `repo_url`.
- Canonical event kinds:
  - `assistant_token`
  - `reasoning_step`
  - `trajectory_step`
  - `status`
  - `warning`
  - `tool_call`
  - `tool_result`
  - `plan_update`
  - `rlm_executing`
  - `memory_update`
  - `hitl_request`
  - `hitl_resolved`
  - `command_ack`
  - `command_reject`
  - terminal: `final`, `cancelled`, `error`
- Every chat event payload should carry normalized runtime metadata under `payload.runtime` when
  runtime state is known. The stable keys are:
  - `runtime_mode`
  - `execution_mode`
  - `depth`
  - `max_depth`
  - `execution_profile`
  - `sandbox_active`
  - `sandbox_id`
  - `effective_max_iters`
  - optional `volume_name`
  - optional `run_id`
- For Daytona, `payload.runtime.volume_name` refers to the workspace-scoped persistent Daytona
  volume when that volume is in use for the run.
- `final` is transcript-oriented. It may include the final assistant text, reasoning summary,
  citations/sources, attachments, and terminal runtime metadata, but it is no longer the canonical
  workbench hydration source for Daytona.
- During the current compatibility window, chat-final payloads may still include legacy
  `run_result` / `final_artifact` data.
- Frontend workbench state may use chat-final only to backfill missing `summary` and
  `final_artifact` after the turn; it must not hydrate rich analyst sections from chat-final.

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

- Dedicated execution/workbench stream for artifact, step, and run-summary visualization.
- Filters by auth-derived identity plus `session_id`.
- `workspace_id` and `user_id` query params are unsupported and should be rejected immediately.
- Emits `execution_started`, `execution_step`, `execution_completed`.
- `execution_completed.summary` is the canonical canvas/workbench summary for both runtimes.
  Frontend workbench state should hydrate from this summary rather than scraping Daytona-only
  fields from `/ws/chat final`.
- `execution_step.step` now carries additive actor metadata:
  - `depth` (optional)
  - `actor_kind` (`root_rlm | sub_agent | delegate | unknown`, optional)
  - `actor_id` (optional)
  - `lane_key` (optional)
- `execution_completed.summary` should be shaped so both Modal/ReAct and Daytona/RLM can hydrate
  the same frontend canvas shell.
- Minimum required top-level fields across both runtime modes:
  - `run_id`
  - `runtime_mode`
  - `task`
  - `final_artifact`
  - `summary`
- `summary` should carry the final termination/error/warnings/duration metadata the workbench needs.
- Richer completion sections remain allowed but optional in this phase:
  - `status`
  - `termination_reason`
  - `duration_ms`
  - `iterations`
  - `callbacks`
  - `prompt_handles` / `prompts`
  - `context_sources`
  - `sources`
  - `attachments`
  - `warnings`
- `execution_step.step.type` remains runtime-agnostic with the current `llm | tool | repl | memory
  | output` lane model.
- `execution_step.step.timestamp` is emitted as numeric Unix epoch seconds by the backend; the frontend may normalize it for display, but should not require ISO strings on the wire.

### Unified Workspace Canvas Contract

- Frontend transcript state and workbench state should reduce into a canonical per-turn record keyed
  by assistant turn / session / run identity.
- The chat transcript remains compact:
  - user message
  - assistant streaming/final answer
  - lightweight trace/status/tool summaries
- The canvas is the primary place for verbose execution detail:
  - `Answer`
  - `Reasoning`
  - `Plan / Trajectory`
  - `Tools / Execution`
  - `Evidence`
  - `Artifacts`
- Daytona-specific iteration/callback/prompt detail is additive inside the canvas. It is not the
  primary cross-runtime reasoning model.
- Daytona live trace should expose the guide-relevant milestones in realtime:
  iteration start, planner/reasoning preview, extracted code preview, sandbox observation, recursive
  child spawn / child synthesis, and terminal completion or failure.
- Daytona sandbox/debug transcript cards are driven by `status` frames whose payload carries
  `phase="sandbox_output"` plus stream text metadata. Treat that wire shape as part of the
  transcript rendering contract while it remains in use.

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
- `VITE_FLEET_TRACE`

Execution stream payload-size controls (backend):

- `WS_EXECUTION_MAX_TEXT_CHARS` (default `65536`)
- `WS_EXECUTION_MAX_COLLECTION_ITEMS` (default `500`)
- `WS_EXECUTION_MAX_RECURSION_DEPTH` (default `12`)

## Validation Checklist

From repo root:

```bash
uv run fleet-rlm serve-api --port 8000
uv run python scripts/openapi_tools.py generate
rg -n "^  /" openapi.yaml
rg -n "@router.websocket" src/fleet_rlm/api/routers/ws/endpoint.py
```

From `src/frontend` (optional frontend validation):

```bash
pnpm install --frozen-lockfile
pnpm run api:check
pnpm run type-check
pnpm run lint:robustness
pnpm run test:unit
pnpm run build
```

## Change Policy

If backend routes or payload shapes change, update this file in the same PR as the code change.

## Frontend API Layer Policy

- Canonical backend contracts for runtime/chat/auth should use `src/frontend/src/lib/rlm-api/*`.
- Legacy `src/frontend/src/lib/api` auth/chat endpoint helpers have been removed. Do not reintroduce auth/chat contracts in that layer.
- Canonical frontend feature ownership now lives in:
  - `src/frontend/src/screens/workspace/*` for the live chat/runtime surface
  - `src/frontend/src/screens/volumes/*` for the volumes browser
  - `src/frontend/src/screens/shell/*` for composed shell navigation widgets
