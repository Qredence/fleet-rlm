# Frontend Back-end Integration

This document captures the current integration contract between the frontend
SPA and the backend API.

The important rule is simple: the frontend talks to the backend through a
small REST surface, a conversational websocket, a separate passive
execution subscription websocket, and an AI SDK UIMessage v1 SSE streaming
endpoint at ``POST /api/chat``. The SSE endpoint exists for future frontend
use and is backend-complete in Phase 1; the frontend does not yet consume it.

## Supported Product Surfaces

The live shell supports:

- `/app/workspace`
- `/app/optimization`
- `/app/volumes`
- `/app/settings`

Legacy `taxonomy`, `skills`, `memory`, `analytics`, and `history` routes are not supported
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
- `GET /api/v1/optimization/status`
- `GET /api/v1/optimization/modules`
- `GET /api/v1/optimization/runs`
- `GET /api/v1/optimization/runs/{run_id}`
- `POST /api/v1/optimization/run`
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
Daytona-backed runtime volume APIs. The optimization surface uses the
GEPA-backed optimization endpoints for status, modules, datasets, runs, and run details.

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

Common frontend-facing event/source categories on this stream:

- assistant text tokens
- reasoning and status updates
- tool call and tool result updates
- sandbox execution and sandbox activity updates
- recursive RLM delegation updates
- MLflow span updates
- turn input summaries
- clarification and HITL command updates
- final, cancelled, and error terminal frames

The backend source of truth is `RuntimeEvent` in `runtime/events.py`, projected
by `api/events/project_chat.py` into websocket frames. Older labels such as
`reasoning_step` or `trajectory_step` may appear only as compatibility labels
inside projected payloads or historical docs; new frontend behavior should
route by the normalized envelope, `payload.source_type`, and structured
payload fields.

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
- `rlm_max_llm_calls` -> `dspy.RLM(max_llm_calls=...)`; this is a
  semantic sub-LM call-count cap for `llm_query*`, not a token budget.
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

Event and log streaming is part of the product UX contract. Backend events
that describe sandbox lifecycle, process logs, bridge callbacks, volume/file
operations, memory access, runtime diagnostics, or artifacts should carry
correlation identifiers when available:

- `run_id`
- `session_id`
- `sandbox_id`
- `child_sandbox_id`
- `process_session_id`
- `command_id`
- `tool_call_id`
- `artifact_id`
- `memory_key`
- `actor_id`
- `parent_id`

Secrets, provider credentials, preview tokens, API keys, and raw environment
values must be redacted before websocket emission. Frontend code should render
redacted values as redacted, not attempt to reconstruct or expose hidden
details from auxiliary payload fields.

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

Future additive event/source types should preserve the existing websocket frame
shape. The preferred direction is to add structured payloads for sandbox
lifecycle, process logs, volume/file events, memory events, bridge callbacks,
runtime diagnostics, and durable artifact references rather than creating a
new transport.

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

Durable artifacts should be rendered from volume-backed references, not from
transient sandbox workspace paths. Markdown/report artifacts should eventually
show compact inline previews in chat and a full rendered/raw preview in the
workspace sidepanel, backed by the same durable volume file APIs used by the
Volume tab.

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

The runtime and LLM Provider Profile forms use the backend settings and provider profiles APIs.

## SSE Streaming: POST /api/chat

Phase 1 adds an AI SDK UIMessage v1 SSE streaming endpoint at ``POST /api/chat``
as a transport boundary over the existing DSPy runtime. See
`ADR-0003 <../adr/0003-api-chat-ai-sdk-uimessage-stream.md>`_ (SSE protocol)
and `ADR-0004 <../adr/0004-chat-execution-context-seam.md>`_ (transport
seam) for the full design rationale.

### Route Details

- **Path:** ``/api/chat`` (mounted at app root, *not* ``/api/v1/chat``)
- **Method:** ``POST`` only (``GET``/``PUT``/``DELETE``/``PATCH`` return ``405
  Method Not Allowed``)
- **Content-Type accepted:** ``application/json``
- **Content-Type returned:** ``text/event-stream``
- **Protocol marker:** ``x-vercel-ai-ui-message-stream: v1``
- **Transfer encoding:** chunked (no ``Content-Length``)
- **Auth:** ``require_http_identity`` (HTTPBearer → ``NormalizedIdentity``);
  local-mode bypass via ``auth_required=false`` returns
  ``build_unauthenticated_identity(cfg)``
- **Cancellation:** ``await request.is_disconnected()`` flips
  ``cancel_flag["cancelled"]``; runtime ``cancel_check`` polls the same flag

### ChatRequest (Request Body)

The endpoint accepts a ``ChatRequest`` JSON body (defined in
``src/fleet_rlm/api/schemas/chat.py``). ``ChatRequest`` uses
``extra="forbid"`` (matching the existing ``WSMessage`` policy) and requires
at least one message.

**``ChatRequest`` fields:**

.. code-block:: python

    class ChatMessage(BaseModel):
        model_config = ConfigDict(extra="forbid")

        role: Literal["user", "assistant", "system", "tool"]
        content: str | None = None
        parts: list[dict] | None = None

    class ChatRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")

        messages: list[ChatMessage]        # min_length=1
        # Optional fleet control fields:
        session_id: str | None = None
        execution_mode: str | None = None  # Legacy: auto/rlm_only/tools_only
        repo_url: str | None = None
        repo_ref: str | None = None
        context_paths: list[str] | None = None
        batch_concurrency: int | None = None
        docs_path: str | None = None
        trace: bool | None = None
        trace_mode: str | None = None
        selected_skill_ids: list[str] | None = None

The endpoint extracts the latest ``role: "user"`` message from
``messages`` (scanning backwards) to feed the turn. If the user message
has ``content=None`` but ``parts`` with a ``type: "text"`` entry, the text
is extracted from parts (AI SDK UIMessage shape).

### SSE Wire Format

Each SSE event line is ``data: {json}\n\n``, terminated by a blank line.
The stream ends with ``data: [DONE]\n\n``.

.. code-block:: text

    Content-Type: text/event-stream
    x-vercel-ai-ui-message-stream: v1

    data: {"type":"start","messageId":"..."}\n\n
    data: {"type":"start-step"}\n\n
    data: {"type":"data-agent","selected_skills":[...],"available_tools":[...]}\n\n
    data: {"type":"text-start"}\n\n
    data: {"type":"text-delta","delta":"Hello"}\n\n
    data: {"type":"text-end"}\n\n
    data: {"type":"finish-step"}\n\n
    data: {"type":"finish"}\n\n
    data: [DONE]\n\n

### Transport / Runtime Seam

Both the SSE endpoint and the existing WebSocket endpoint
(``/api/v1/ws/execution``) share a transport-neutral seam defined by
``ChatExecutionContext`` and ``stream_turn()`` (in
``src/fleet_rlm/api/runtime_services/``). Each transport builds a
``ChatExecutionContext`` from its inputs and calls the single
``stream_turn()`` function.

### Phase 2A: ExecutionBackend Seam

Phase 2A introduces an ``ExecutionBackend`` selector and dispatch point
behind ``stream_turn()``, adding the ability to choose which runtime backend
executes a turn — while keeping the transport, schema, OpenAPI, and frontend
unchanged.

**Key points:**

- **Server-side only.** ``execution_backend`` is **not** accepted from
  ``ChatRequest`` or frontend clients. It is controlled solely by server
  configuration (``AppConfig.execution_backend``, env variable
  ``EXECUTION_BACKEND``) and internal per-turn overrides
  (``TurnControls.execution_backend``).
- **Default is ``legacy_agent_runtime``.** Every existing Phase 1 call path
  resolves to this backend, preserving 100% of Phase 1 behavior.
- **``direct_rlm`` is promotion-gated and currently opt-in.** Set ``EXECUTION_BACKEND=direct_rlm``
  server-side. Introduced as a stub in Phase 2A; dispatches to
  ``DirectRLMRunner`` as of Phase 2B; runs an opt-in golden path through
  ``dspy.RLM`` and the pooled Daytona interpreter as of Phase 2C; emits
  ``TURN_INPUTS``, trajectory replay events, ``TEXT``, structured ``ERROR``,
  and enriched ``DONE`` metadata as of Phase 2D. Not the default; not exposed
  on ``ChatRequest``.
- **Resolution order.** Inside ``stream_turn()``:
  1. ``ctx.controls.execution_backend`` if not ``None`` (per-request override)
  2. ``AppConfig.execution_backend`` (process default from env / config)
- **``execution_backend`` is orthogonal to ``execution_mode``.**
  ``ExecutionBackend`` selects *which runtime*; ``ExecutionMode`` selects
  *how the legacy runtime behaves*.

See ``docs/adr/0005-execution-backend-seam.md`` for the full design rationale.

.. code-block:: python

    @dataclass(slots=True)
    class TurnControls:
        execution_backend: ExecutionBackend | None = None
        execution_mode: str | None = None
        repo_url: str | None = None
        repo_ref: str | None = None
        context_paths: list[str] = field(default_factory=list)
        batch_concurrency: int | None = None
        docs_path: str | None = None
        trace: bool | None = None
        trace_mode: str | None = None
        selected_skill_ids: list[str] = field(default_factory=list)

    @dataclass(slots=True)
    class ChatExecutionContext:
        prepared: PreparedChatRuntime
        identity: NormalizedIdentity
        session_id: str | None
        canonical_workspace_id: str | None
        canonical_user_id: str | None
        owner_tenant_claim: str | None
        owner_user_claim: str | None
        cancel_flag: dict[str, bool]
        controls: TurnControls

    async def stream_turn(
        *,
        ctx: ChatExecutionContext,
        agent_runtime: AgentRuntime,
        message: str,
    ) -> AsyncIterator[RuntimeEvent]:
        ...

``stream_turn()`` resolves the execution backend once at the top (per-request
``TurnControls.execution_backend`` if not ``None``, else ``AppConfig.execution_backend``)
and dispatches via ``if/elif``. The default backend ``legacy_agent_runtime``
delegates to the explicit ``agent_runtime.aiter_chat_turn_stream()`` with a
``cancel_check`` reading ``ctx.cancel_flag`` and an explicit allowlist of
legacy runtime kwargs: ``trace``, ``docs_path``, ``repo_url``, ``repo_ref``,
``context_paths``, and ``batch_concurrency``. ``trace_mode`` is accepted by the
transport/context layer but is not currently forwarded to the legacy
``AgentRuntime.aiter_chat_turn_stream()``; future direct runtime
implementations may consume it explicitly. ``ctx.prepared.planner_lm`` remains
the DSPy planner LM and is not the AgentRuntime. The second backend
``direct_rlm`` dispatches to ``DirectRLMRunner`` (Phase 2B+), which runs one
real RLM turn and emits ``TURN_INPUTS``, trajectory replay, ``TEXT``,
structured ``ERROR``, and enriched ``DONE`` (Phase 2D). It is promotion-gated,
currently opt-in (``EXECUTION_BACKEND=direct_rlm``), and not exposed on
``ChatRequest``.

### RuntimeEventKind → AI SDK UIMessage v1 Part Mapping

The SSE projector (``project_sse()`` in
``src/fleet_rlm/api/events/project_sse.py``) maps all 14
``RuntimeEventKind`` values to AI SDK UIMessage v1 parts. Terminal events
(``DONE``, ``ERROR``, cancellation) emit ``[DONE]`` as the final line.

.. list-table::
    :header-rows: 1

    * - RuntimeEventKind
      - AI SDK v1 part(s)
    * - ``TEXT``
      - ``text-start`` / ``text-delta`` / ``text-end``
    * - ``REASONING``
      - ``reasoning-start`` / ``reasoning-delta`` / ``reasoning-end``
    * - ``TOOL_CALL``
      - ``tool-input-start`` / ``tool-input-available``
    * - ``TOOL_RESULT``
      - ``tool-output-available``
    * - ``TURN_STARTED``
      - ``start`` (messageId) / ``start-step`` / ``data-agent``
    * - ``TURN_INPUTS``
      - ``data-turn-inputs``
    * - ``SANDBOX_EXEC``
      - ``data-sandbox-exec``
    * - ``RLM_DELEGATE``
      - ``data-rlm-delegate``
    * - ``MLFLOW_SPAN``
      - ``data-span``
    * - ``STATUS``
      - ``data-status``
    * - ``WARNING``
      - ``data-warning``
    * - ``CLARIFICATION``
      - ``data-clarification``
    * - ``DONE``
      - ``finish-step`` / ``finish`` / ``[DONE]``
    * - ``ERROR``
      - ``error`` / ``[DONE]``
    * - client disconnect / cancel
      - ``abort`` / ``[DONE]``

Additional ``data-*`` parts are projected from payload fields alongside the
primary mapping (never suppressing it):

- ``data-artifact`` — generated file artifact (title, content_type, path)
- ``data-task`` — task progress
- ``data-performance`` — trace performance summary
- ``data-suggestion`` — suggested next actions

No kind is silently dropped. ``TEXT`` and ``REASONING`` use start/delta/end
wrappers. ``tool-input-delta`` is not emitted in Phase 1.

### Implementation Modules

| Component | Module | Responsibility |
|---|---|---|
| ``ChatRequest`` / ``ChatMessage`` | ``api/schemas/chat.py`` | Pydantic request models |
| ``ChatExecutionContext`` / ``TurnControls`` | ``api/runtime_services/chat_context.py`` | Transport-neutral context |
| ``stream_turn()`` | ``api/runtime_services/stream_turn.py`` | Transport-neutral turn stream |
| ``project_sse()`` | ``api/events/project_sse.py`` | ``RuntimeEvent`` → SSE lines |
| ``POST /api/chat`` handler | ``api/routers/chat.py`` | SSE endpoint, auth, cancellation |
| Route registration | ``api/main.py`` | ``app.include_router(chat.router)`` (after ``api_v1``) |

### Session Contract

- ``session_id`` absent → new session created, id surfaced in ``data-agent``.
- ``session_id`` present → existing session restored (if found); non-existent
  id creates a new session with that id (pinned behavior).
- Each ``POST`` is non-idempotent: two identical requests create distinct runs
  with distinct ``messageId`` values. No ``Idempotency-Key`` header supported
  in Phase 1.

### Relationship to WebSocket Endpoint

The SSE endpoint is a parallel transport that shares the same runtime seam.
The existing WebSocket endpoint (``/api/v1/ws/execution``) and its
``project_chat()`` frame projection remain entirely unchanged — same
behavior, same frame schema version 3, same projection. The two transports
differ *only* in how they project the underlying ``RuntimeEvent`` stream:
WebSocket uses ``project_chat()`` (custom WS frames), SSE uses
``project_sse()`` (AI SDK v1 parts).
