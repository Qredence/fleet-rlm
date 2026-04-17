# Frontend Back-end Integration

This document captures the current integration contract between the frontend
SPA and the backend API.

The important rule is simple: the frontend talks to the backend through a
small REST surface, a conversational websocket, and a separate passive
execution subscription websocket. There is no SSE path in the current frontend
contract.

## Supported Product Surfaces

The live shell supports:

- `/app/workspace`
- `/app/volumes`
- `/app/history`
- `/app/optimization`
- `/app/settings`

Legacy `taxonomy`, `skills`, `memory`, and `analytics` routes are not supported
entrypoints.

## REST Surfaces Used By The Frontend

The frontend consumes the following backend surfaces:

- `GET /health`
- `GET /ready`
- `GET /api/v1/auth/me`
- `GET /api/v1/runtime/settings`
- `PATCH /api/v1/runtime/settings`
- `POST /api/v1/runtime/tests/lm`
- `POST /api/v1/runtime/tests/daytona`
- `GET /api/v1/runtime/status`
- `GET /api/v1/sessions/state`
- `GET /api/v1/sessions`
- `GET /api/v1/sessions/{id}`
- `GET /api/v1/sessions/{id}/turns`
- `DELETE /api/v1/sessions/{id}`
- `POST /api/v1/traces/feedback`

The history surface uses the sessions endpoints. The settings surface uses the
runtime settings and runtime status endpoints. The workspace surface uses
`/api/v1/auth/me` and runtime status to gate the composer and warnings.

## Websocket Split

The client derives two websocket URLs from the API base:

- `wsUrl` -> `/api/v1/ws/execution`
- `wsExecutionUrl` -> `/api/v1/ws/execution/events`

`VITE_FLEET_WS_URL` can override the base websocket host. If it is unset, the
frontend derives both websocket URLs from `VITE_FLEET_API_URL`.

### `/api/v1/ws/execution`

This is the conversational websocket. It handles:

- `message`
- `cancel`
- `command`

The frontend sends the first user turn here, including:

- `content`
- `trace`
- `trace_mode`
- `execution_mode`
- `repo_url`
- `repo_ref`
- `context_paths`
- `batch_concurrency`
- `analytics_enabled`
- `session_id`

Important rules:

- `session_id` is carried on the message and command payloads.
- `workspace_id` and `user_id` are not supported on websocket payloads.
- Query-string `session_id` is intentionally not part of this route.
- `resolve_hitl` is the command currently used by the workspace HITL flow.

Common event kinds on this stream:

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
- `final`
- `cancelled`
- `error`

### `/api/v1/ws/execution/events`

This is the passive execution subscription stream.

Important rules:

- `session_id` is required as a query parameter.
- The stream is subscription-only.
- It does not accept `message`, `cancel`, or `command` frames.
- It emits execution lifecycle frames for the workbench canvas.

## Runtime And Workbench Contract

The workspace runtime is Daytona-backed and the UI treats `daytona_pilot` as
the public runtime label.

The frontend keeps the following runtime controls aligned with backend requests:

- `execution_mode`
- `repo_url`
- `repo_ref`
- `context_paths`
- `batch_concurrency`

The backend enriches frames with runtime context. The frontend treats these keys
as stable when present:

- `depth`
- `max_depth`
- `execution_profile`
- `sandbox_active`
- `effective_max_iters`
- `volume_name`
- `execution_mode`
- `runtime_mode`
- `sandbox_id`
- `workspace_path`
- `sandbox_transition`

### Transcript Stream

`/api/v1/ws/execution` feeds the live transcript.

The frontend reduces frames into:

- user and assistant messages
- reasoning and trajectory rows
- tool and sandbox cards
- HITL / clarification cards
- summary rows and warnings

The adapter stack is:

1. `ws-frame-parser.ts` normalizes raw websocket frames.
2. `backend-chat-event-adapter.ts` turns chat frames into transcript rows.
3. `backend-artifact-event-adapter.ts` turns execution steps into artifact rows.
4. `chat-display-items.ts` groups rows into assistant turns.

### Workbench Hydration

The workbench panel is summary-driven.

The canonical hydration path is:

1. `ws-frame-parser.ts` converts `execution_completed` frames into a normalized
   event envelope.
2. `run-workbench-hydration.ts` merges `summary`, `final_artifact`, run
   metadata, prompts, iterations, callbacks, sources, and attachments into the
   workbench state.
3. `run-workbench-store.ts` keeps the canonical run panel state in Zustand.

Rules:

- `execution_completed.summary` is the primary completion source.
- `final_artifact` is the primary artifact source.
- Chat-final `run_result` is only a narrow compatibility backfill path.
- The workbench should not depend on transcript scraping for its canonical
  completion state.

## Session And History Contract

The history surface uses both backend sessions and local conversation state.

Backend session data supports:

- session list
- session detail
- session turns
- session deletion

The local conversation store remains a UI-level feature for saved workspace
sessions and does not replace the backend session history.

## Settings Contract

Runtime settings writes are local-only in the current frontend contract.

The settings feature treats these operations as current:

- read current runtime settings
- save runtime settings
- test LM connectivity
- test Daytona connectivity
- refresh runtime status

The runtime and LiteLLM forms use the backend runtime settings API. The
optimization section reuses the optimization feature form rather than duplicating
its own settings implementation.
