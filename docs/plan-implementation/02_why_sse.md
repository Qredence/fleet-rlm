# Why Fleet Is Moving Toward SSE for Chat

## One-sentence rationale

Fleet uses `/api/chat` SSE because chat output is fundamentally a one-way server-to-client stream of assistant text, reasoning/status metadata, and structured runtime events, and SSE is a simpler, more compatible fit for that than a full bidirectional WebSocket chat transport.

## What SSE is used for

SSE is the canonical chat transport:

```text
POST /api/chat
  -> AI SDK UIMessage v1 SSE stream
```

It carries projected chat output from backend `RuntimeEvent` objects into UI-compatible stream parts.

## What WebSocket remains for

WebSocket should not disappear immediately.

WebSocket remains useful for:

- terminal-like interaction;
- live sandbox control;
- bidirectional execution channels;
- compatibility with existing runtime views;
- legacy execution paths during migration.

The refactor is not “SSE everywhere.” It is:

```text
SSE for chat transcript streaming.
WebSocket for interactive control surfaces where bidirectionality matters.
```

## Why SSE is better for main chat

### 1. Chat streaming is mostly one-way

The assistant stream flows from server to client:

```text
assistant text
reasoning/status updates
tool results
artifact refs
done/error events
```

The client does not need a constantly open bidirectional protocol for the ordinary chat response path.

### 2. SSE is easier to reason about

SSE gives Fleet a clear split:

```text
RuntimeEvent -> backend projection -> AI SDK UIMessage stream
```

The runtime does not need to know about WebSocket-specific framing.

### 3. SSE fits AI SDK UIMessage streaming

The UI can consume a standard streamed chat response, while the backend keeps runtime details hidden behind `project_sse.py`.

The desired boundary is:

```text
runtime emits RuntimeEvent
project_sse.py maps RuntimeEvent to AI SDK stream
frontend consumes UIMessage parts
```

### 4. SSE reduces frontend/runtime coupling

With WebSocket-first chat, frontend code can drift into interpreting backend-specific runtime messages.

With SSE-first chat, the backend owns projection into frontend-safe stream parts.

That matters for Phase 6 because trace/debug/performance mapping should be canonical on the backend, not guessed in the frontend.

### 5. SSE is easier to deploy and debug

SSE uses normal HTTP semantics:

- normal `POST /api/chat` request;
- normal auth middleware;
- normal HTTP errors before stream starts;
- normal load balancer/proxy behavior;
- easier request logging;
- easier client retry/error handling.

This is especially useful for errors that should happen before the stream opens, such as invalid attachment refs or prepare/startup failures.

## Why not remove WebSocket now

WebSocket remains a compatibility and control surface.

Do not remove it until:

- direct RLM has parity;
- trace/debug/performance is backend-owned;
- frontend SSE migration is complete;
- terminal/live sandbox control has a clear transport boundary;
- old clients have a migration path.

## Final transport state

The intended final transport model is:

```text
/api/chat SSE
  canonical chat transcript transport

WebSocket
  terminal/live sandbox/bidirectional control compatibility
```

## SSE acceptance criteria

SSE is correct when:

- `project_sse.py` has no backend-specific branches;
- both legacy and direct RLM can emit `RuntimeEvent` objects;
- `/api/chat` streams AI SDK-compatible parts;
- invalid request/setup errors fail before opening the stream;
- WebSocket remains supported during migration;
- frontend rendering depends on backend-projected event types, not runtime internals.
