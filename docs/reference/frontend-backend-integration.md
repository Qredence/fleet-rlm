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
- `/app/settings`

Legacy `taxonomy`, `skills`, `memory`, `analytics`, `history`, and `optimization` routes are not supported
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
- `GET /api/v1/runtime/volume/tree`
- `GET /api/v1/runtime/volume/file`
- `GET /api/v1/sessions/state`
- `GET /api/v1/sessions`
- `GET /api/v1/sessions/{id}`
- `GET /api/v1/sessions/{id}/turns`
- `DELETE /api/v1/sessions/{id}`
- `POST /api/v1/traces/feedback`

The history surface uses the sessions endpoints. The settings surface uses the
runtime settings and runtime status endpoints. The workspace surface uses
`/api/v1/auth/me` and runtime status to gate the composer and warnings. The
workspace sidepanel `Volume` tab and the `/app/volumes` route both use the
Daytona-backed runtime volume APIs.

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
- `mlflow_span`
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
- It emits execution lifecycle frames for workbench hydration.

## Runtime And Workbench Contract

The workspace runtime is Daytona-backed. Request-side provider labels are not
part of the public frontend/backend contract.

The frontend keeps the following runtime controls aligned with backend requests:

- `execution_mode`
- `repo_url`
- `repo_ref`
- `context_paths`
- `batch_concurrency`

When `execution_mode` is `auto`, prompts that combine a public HTTP(S) URL
with documentation-analysis intent (`analyze`, `summarize`, `read`, `docs`, or
`documentation`) route directly to the Daytona-backed RLM document path. The
backend fetches the document through the redirect-validating document helpers
and passes `source_url`, `document_text`, and `source_metadata` as separate
variable-mode `dspy.RLM` inputs. That keeps large documentation bodies in REPL
variables instead of folding them into the prompt text.
`execution_mode="rlm_only"` still forces RLM execution, while
`execution_mode="tools_only"` bypasses the automatic URL-to-RLM route.

Fleet's RLM prompt envelope follows the Fast-RLM usage pattern for large
variable-mode tasks: repeat the task at the top and bottom, keep bulk data in
REPL variables, make available tools ordinary Python callables, and keep
intermediate printed output bounded. The server runtime settings feed the chat
agent's RLM wrappers directly:

- `rlm_max_iterations` -> `dspy.RLM(max_iterations=...)`
- `rlm_max_llm_calls` -> `dspy.RLM(max_llm_calls=...)`
- `agent_max_output_chars` -> `dspy.RLM(max_output_chars=...)`; defaults to 5000
  characters per REPL step so repeated sandbox iterations do not fold large
  file or diff dumps back into every follow-up prompt.

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
- `selected_skills`
- `routing_decision`
- `source_url`
- `trajectory_index`
- `rlm_limits`

### Transcript Stream

`/api/v1/ws/execution` feeds the live transcript.

The frontend reduces frames into:

- user and assistant messages
- reasoning and trajectory rows
- tool and sandbox cards
- selected-skill and routing status rows
- HITL / clarification cards
- summary rows and warnings

RLM trajectories that include `{reasoning, code, output}` are normalized into
`execution_step` frames with `step.type="repl"`. The transcript renders these
as compact expandable sandbox rows; large code/output payloads stay summarized
in chat while the workbench receives the structured step payload.

Curated Fleet/DSPy MLflow span activity is surfaced as `execution_step` frames
with `payload.source_type="mlflow_span"`. Each span payload must include a
stable `span_id`, display `name`, and lifecycle `status` of `started`,
`completed`, or `error`; optional detail fields such as `input`, `output`,
`error`, and `metadata` are sanitized by the backend before websocket
projection. The frontend pairs lifecycle updates by `span_id` and renders them
through Agent Elements as a grouped execution activity with redacted expandable
details and an MLflow trace link when `trace_id` is available.

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

### Workspace Sidepanel

Workspace chat is primary. The workspace sidepanel is local to
`/app/workspace`, collapsible, and resizable. It exposes exactly three tabs:

- `Trajectories`
- `Graph`
- `Volume`

`Trajectories` and `Graph` resolve the active run by durable chat session id
first. When runtime metadata exposes `external_session_id`, the frontend and
backend may use it as the MLflow/runtime trace alias. Missing MLflow data must
not make the sidepanel unusable; the frontend falls back to live transcript
rows and artifact summaries already available in workspace state.

`Volume` uses `GET /api/v1/runtime/volume/tree` and
`GET /api/v1/runtime/volume/file` for Daytona-backed browsing, inline preview,
and the resizable tree/preview split. `/app/volumes` remains the full-page
durable volume browser.

## Session Contract

The workbench session controls use both backend sessions and local conversation state.

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

The runtime and LiteLLM forms use the backend runtime settings API.
