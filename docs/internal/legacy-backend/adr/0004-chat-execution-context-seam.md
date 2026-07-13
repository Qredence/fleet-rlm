# ChatExecutionContext is the transport-neutral seam

## Context

The chat runtime was reachable only via the websocket endpoint
`/api/v1/ws/execution`. Runtime preparation (`PreparedChatRuntime`),
session state (`ChatSessionState`), identity (`NormalizedIdentity`), and
per-turn controls (`execution_mode`, `repo_url`, `repo_ref`,
`context_paths`, `batch_concurrency`, `docs_path`, `trace`, `trace_mode`,
`selected_skill_ids`) were threaded through WS-coupled functions
(`prepare_chat_runtime(websocket=...)`, `stream_agent_turn()` in
`ws/stream_events.py`). Phase 1 adds `POST /api/chat` SSE alongside the WS
endpoint; both must drive the same DSPy runtime without duplicating the
preparation/turn logic.

## Decision

Introduce a transport-neutral `ChatExecutionContext` dataclass as the single
shared seam between transport and runtime. Both the WS and SSE paths build it
from their transport-specific inputs and pass it to one shared function:

```python
async def stream_turn(
    *,
    ctx: ChatExecutionContext,
    agent_runtime: AgentRuntime,
    message: str,
) -> AsyncIterator[RuntimeEvent]:
    ...
```

`ChatExecutionContext` composes existing cohesive objects rather than
flattening them, with per-turn fields isolated in a `TurnControls` sub-object:

```python
@dataclass(slots=True)
class TurnControls:
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
```

`PreparedChatRuntime` is kept intact because it already groups cohesive
runtime dependencies (config, planner/delegate LMs, repository, persistence,
identity rows). Per-turn fields live in `TurnControls` because they vary per
request/message and must not be confused with prepared runtime dependencies.

## Considered Options

- **Flatten everything into one dataclass.** Rejected: loses the cohesion of
  `PreparedChatRuntime` and conflates per-turn controls with runtime
  dependencies.
- **Keep `PreparedChatRuntime` and `ChatSessionState` separate; `stream_turn`
  takes both.** Rejected: two transport-neutral objects plus identity plus
  controls is the same threading problem rearranged; one context is simpler
  for both transports.

## Consequences

- `stream_turn(*, ctx, agent_runtime, message)` is the single runtime entry
  point shared by both transports; the WS path's `stream_agent_turn()` is
  refactored to delegate to it.
- `PreparedChatRuntime.planner_lm` remains the prepared DSPy planner model; it
  is not the AgentRuntime. Transports pass the context-managed AgentRuntime-like
  object explicitly as `agent_runtime`.
- `trace_mode` and `selected_skill_ids` remain accepted at the transport and
  `TurnControls` layer, but the legacy AgentRuntime backend forwards only the
  kwargs supported by `AgentRuntime.aiter_chat_turn_stream()`. Future direct
  runtime backends may consume those context-only controls explicitly.
- `prepare_chat_runtime()` loses its `WebSocket` parameter and returns
  `PreparedChatRuntime` from transport-neutral inputs; both transports wrap it
  into a `ChatExecutionContext`.
- The SSE projector and the WS `project_chat()` both consume the
  `AsyncIterator[RuntimeEvent]` from `stream_turn()`; neither owns runtime
  logic.
- Adding a third transport later means building a `ChatExecutionContext` and
  calling `stream_turn()` — no runtime changes.
