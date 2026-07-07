# /api/chat uses AI SDK UIMessage Data Stream Protocol v1

## Context

Phase 1 adds a FastAPI `POST /api/chat` SSE endpoint as a transport boundary
over the existing DSPy runtime. The frontend previously consumed only custom
websocket frames via `streamChatOverWs()` and a fleet-specific
`backend-chat-event-adapter.ts`; it did not use AI SDK `useChat()` and had no
`@ai-sdk/*` runtime dependency. The runtime side
(`AgentRuntime.aiter_chat_turn_stream()` yielding `RuntimeEvent`) was already
transport-neutral, so the open question was the wire shape the new SSE
endpoint should emit.

## Decision

`POST /api/chat` returns an SSE response compatible with AI SDK UI `useChat`,
using the `x-vercel-ai-ui-message-stream: v1` stream protocol. Fleet runtime
events are projected into AI SDK stream parts: standard parts (`text-delta`,
`tool-*`, `reasoning`, `finish`, `error`, etc.) where they fit, and custom
`data-*` parts for fleet-specific metadata that has no standard equivalent —
trace spans, artifacts, tasks, performance summaries, selected skills, and
suggestions.

The existing websocket endpoint (`/api/v1/ws/execution`) and its
`project_chat()` frame projection remain unchanged. The new SSE projector
lives alongside `project_chat()` as a parallel projection of the same
`RuntimeEvent` stream.

## Considered Options

- **Mirror the existing WS frame format over SSE.** Rejected: the goal is AI
  SDK `useChat` compatibility, which requires the UIMessage data stream
  protocol shape; mirroring WS frames would force a custom frontend client
  and defeat the transport-boundary purpose.
- **AI SDK v3 / older data stream protocol.** Rejected: `v1` of the UIMessage
  stream is the current AI SDK UI protocol; targeting an older version would
  require a migration later.
- **Emit only standard AI SDK parts, drop fleet metadata.** Rejected: trace
  spans, artifacts, selected skills, and performance summaries are
  load-bearing for fleet's observability and UX; the `data-*` custom-part
  extension is the protocol-sanctioned way to carry them.

## Consequences

- The frontend gains a `@ai-sdk/*` transport dependency (or a thin
  `useChat`-compatible client) and an SSE adapter path alongside the existing
  WS adapter; both consume the same `RuntimeEvent` semantics via different
  projections.
- The SSE projector must map every `RuntimeEventKind` (15 kinds) to either a
  standard AI SDK part or a documented `data-*` custom part; unmapped kinds
  must not be silently dropped.
- `x-vercel-ai-ui-message-stream: v1` is set as the SSE response content-type
  header / protocol marker so AI SDK clients negotiate correctly.
- The WS path is unaffected; the two transports share `ChatExecutionContext`
  and the transport-neutral `stream_turn()` seam, but each owns its own
  projector.

## RuntimeEventKind → AI SDK UIMessage stream part mapping

Every `RuntimeEventKind` (15 kinds in `runtime/events.py`) maps to either a
standard AI SDK UIMessage/Data Stream part or a Fleet `data-*` custom part.
No kind is silently dropped. Terminal events emit `finish`/`error`/`abort`
followed by a `data: [DONE]` line.

```text
TEXT          -> text-start / text-delta / text-end
REASONING     -> reasoning-start / reasoning-delta / reasoning-end
TOOL_CALL     -> tool-input-start / tool-input-available
                 (toolCallId, toolName, input from RuntimeToolInfo)
TOOL_RESULT   -> tool-output-available
SANDBOX_EXEC  -> data-sandbox-exec
                 (sandbox_id, command/code preview, stdout_preview,
                 stderr_preview, exit_code, duration_ms)
RLM_DELEGATE  -> data-rlm-delegate
                 (actor, recursion depth, child sandbox id, delegate status,
                 output preview)
MLFLOW_SPAN   -> data-span  (trace/span metadata for Fleet trace rendering)
TURN_STARTED  -> start / start-step / data-agent
                 (selected profile, selected skills, available tools,
                 execution mode, session/run ids)
TURN_INPUTS   -> data-turn-inputs  (assembled input rows / context preview)
STATUS        -> data-status
WARNING       -> data-warning
CLARIFICATION -> data-clarification
DONE          -> finish  +  data: [DONE]
ERROR         -> error    +  data: [DONE]
client disconnect / cancel -> abort  +  data: [DONE]
```

Additional Fleet `data-*` custom parts (not driven 1:1 by a RuntimeEventKind
but projected from payload fields per PLANS.md §21 and the foundation text):

```text
data-artifact      generated/updated file artifact (title, content_type, path)
data-task          task progress
data-performance   trace performance summary
data-suggestion    suggested next actions
```

Protocol rules:

- `TEXT` and `REASONING` use start/delta/end wrappers, not bare deltas.
- `finish` means the assistant message completed normally. Fatal runtime
  errors emit `error` then `[DONE]`; they do not pretend to be `finish` unless
  the runtime already treats that terminal event as a completed turn.
- Client disconnect / cancellation emits `abort` then `[DONE]`.
